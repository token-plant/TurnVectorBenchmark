"""Verify a reconciled source mapping against an on-disk Git repository.

Every Git child process is hardened: it runs the frozen qualified Xcode Git
executable with a sanitized environment, neutralized ambient config/attributes/
excludes/fsmonitor/untracked-cache state, replace objects disabled, optional
locks and prompts disabled, a frozen per-command timeout, and bounded output.
This is the PR 2 verifier; the full PR 7 repository-control descriptor/config
grammar is intentionally not implemented here.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .core import ContractError
from .source_reconciliation import SourceReconciliation

_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")

# The frozen contract admits exactly one qualified Git executable: the Xcode
# 26.5 Git 2.50.1 binary and its exec tree. A PATH-selected shim is never used.
QUALIFIED_GIT = "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/bin/git"
GIT_EXEC_PATH = "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core"

# Frozen per-command timeout and output bounds (repository_control caps).
GIT_TIMEOUT_SECONDS = 60
GIT_STDOUT_BYTES_MAX = 4 * 1024 * 1024
GIT_STDERR_BYTES_MAX = 1024 * 1024

# The exact controlled child environment, frozen in docs/D0-AUTHORITY-DESIGN.md
# ("Sanitized Child Environment"). It is passed verbatim to every qualified Git
# child: exactly these 12 keys with exactly these values, and no caller
# variable of any kind (DEVELOPER_DIR, DYLD_*, GIT_*, LC_*, PYTHON*, SSH_*,
# XDG_*, locale, shell, or inherited Git state) is inherited. GIT_EXEC_PATH is
# pinned at invocation time from the current module constant (never from the
# ambient environment), so production always uses the frozen qualified exec
# root while tests can inject the runner's.
_GIT_CHILD_ENV = {
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_EXEC_PATH": GIT_EXEC_PATH,
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": "/var/empty",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core:/usr/bin:/bin",
    "TZ": "UTC",
}

# Read-only command-wide overrides that neutralize config-selected ambient
# behavior for the admitted read-only grammar (cat-file, rev-parse,
# merge-base, status). Repo-local .gitignore/.gitattributes stay repository
# state; only ambient sources are suppressed.
_GIT_CONFIG_OVERRIDES = (
    "core.attributesFile=/dev/null",
    "core.excludesFile=/dev/null",
    "core.fsmonitor=false",
    "core.untrackedCache=false",
)


@dataclass(frozen=True)
class SourceVerification:
    observed_revision: str
    revision_relation: str
    mapping_count: int


def _git_argv(root: Path, *args: str) -> List[str]:
    argv = [QUALIFIED_GIT]
    for override in _GIT_CONFIG_OVERRIDES:
        argv.extend(("-c", override))
    argv.extend(("-C", str(root)))
    argv.extend(args)
    return argv


def _kill_and_reap(proc: "subprocess.Popen[bytes]") -> None:
    """Kill (if still running) and reap the child so no zombie survives.

    The poll/kill exit race is tolerated: the child may exit between the
    poll() that reports it still running and the kill() call, in which case
    kill() raises ProcessLookupError and the following wait() still reaps the
    already-exited child. Any other OSError from kill or from the reap wait
    means termination did not provably succeed, so it becomes a fixed
    non-leaking ContractError instead of a silent successful-reap claim; the
    caller's original timeout/overflow ContractError is preserved only when
    the child was actually terminated and reaped.
    """
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:  # pragma: no cover - poll/kill exit race
            pass
        except OSError as exc:
            raise ContractError("git child termination failed") from exc
    try:
        proc.wait()
    except OSError as exc:
        raise ContractError("git child termination failed") from exc


def _drain_bounded(
    proc: "subprocess.Popen[bytes]",
    stdout_cap: int,
    stderr_cap: int,
    timeout: float,
) -> Tuple[bytes, bytes]:
    """Drain both pipes concurrently, retaining at most cap+1 bytes each.

    Incremental non-blocking reads consume both streams together so a child
    cannot deadlock on a full pipe. The deadline and the per-stream caps are
    enforced against live output: on timeout or overflow the child is killed
    and reaped and a ContractError is raised that never carries captured
    bytes, so child output cannot leak into an error. The single deadline
    also covers final process termination: a child that closes both pipes
    while still running (for example a sleep) is killed when the deadline
    expires rather than being waited on indefinitely.
    """
    streams = [
        (proc.stdout, "stdout", stdout_cap, bytearray()),
        (proc.stderr, "stderr", stderr_cap, bytearray()),
    ]
    active = [entry for entry in streams if entry[0] is not None]
    deadline = time.monotonic() + timeout
    while active:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_and_reap(proc)
            raise ContractError("git invocation timed out")
        try:
            readable, _, _ = select.select(
                [entry[0] for entry in active], [], [], remaining
            )
        except OSError as exc:
            if exc.errno == errno.EINTR:  # pragma: no cover - signal race
                continue
            _kill_and_reap(proc)
            raise ContractError("git pipe read failed") from exc
        for pipe in readable:
            entry = next(item for item in active if item[0] is pipe)
            _, name, cap, buffer = entry
            try:
                chunk = os.read(
                    pipe.fileno(), min(65536, cap + 1 - len(buffer))
                )
            except OSError as exc:
                _kill_and_reap(proc)
                raise ContractError("git pipe read failed") from exc
            if not chunk:
                active.remove(entry)
                continue
            buffer.extend(chunk)
            if len(buffer) > cap:
                _kill_and_reap(proc)
                raise ContractError(f"git produced over-bounded {name}")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _kill_and_reap(proc)
        raise ContractError("git invocation timed out")
    try:
        proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _kill_and_reap(proc)
        raise ContractError("git invocation timed out")
    return bytes(streams[0][3]), bytes(streams[1][3])


def _run(argv: List[str], root: Path) -> "subprocess.CompletedProcess[bytes]":
    env = dict(_GIT_CHILD_ENV)
    env["GIT_EXEC_PATH"] = GIT_EXEC_PATH
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=root,
            env=env,
            close_fds=True,
        )
    except OSError as exc:  # pragma: no cover - environment failure
        raise ContractError("git invocation failed") from exc
    try:
        stdout, stderr = _drain_bounded(
            proc, GIT_STDOUT_BYTES_MAX, GIT_STDERR_BYTES_MAX, GIT_TIMEOUT_SECONDS
        )
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    finally:
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:  # pragma: no cover - already-closed pipe
                    pass


def _decode(output: bytes, what: str) -> str:
    try:
        text = output.decode("utf-8").strip()
    except UnicodeError as exc:  # pragma: no cover - malformed output
        raise ContractError(f"{what} produced undecodable output") from exc
    if not text:
        raise ContractError(f"{what} produced empty output")
    return text


def _cat_blob(root: Path, revision: str, path: str, adr: str, side: str) -> bytes:
    proc = _run(_git_argv(root, "cat-file", "blob", f"{revision}:{path}"), root)
    if proc.returncode != 0:
        raise ContractError(f"missing {side} blob for ADR {adr}")
    return proc.stdout


def verify_source_reconciliation(
    record: SourceReconciliation, target_repo: Path
) -> SourceVerification:
    try:
        root = target_repo.resolve()
    except (OSError, RuntimeError) as exc:
        raise ContractError("cannot resolve target repository") from exc

    proc = _run(_git_argv(root, "rev-parse", "--show-toplevel"), root)
    if proc.returncode != 0:
        raise ContractError("rev-parse --show-toplevel failed")
    try:
        toplevel = Path(_decode(proc.stdout, "rev-parse --show-toplevel")).resolve()
    except (OSError, RuntimeError) as exc:
        raise ContractError("cannot resolve git toplevel") from exc
    if toplevel != root:
        raise ContractError("toplevel does not resolve to the passed root")
    if toplevel.name != record.target_source.repository:
        raise ContractError("toplevel basename does not match target repository")

    proc = _run(_git_argv(root, "rev-parse", "HEAD"), root)
    if proc.returncode != 0:
        raise ContractError("rev-parse HEAD failed")
    head = _decode(proc.stdout, "rev-parse HEAD")
    if not _HEAD_RE.fullmatch(head):
        raise ContractError("HEAD is not lowercase 40 hex")

    target_revision = record.target_source.revision
    if head == target_revision:
        revision_relation = "exact"
    else:
        proc = _run(
            _git_argv(
                root, "merge-base", "--is-ancestor", target_revision, head
            ),
            root,
        )
        if proc.returncode == 0:
            revision_relation = "descendant"
        elif proc.returncode == 1:
            raise ContractError("target revision is unrelated to HEAD")
        else:
            raise ContractError("merge-base inspection failed")

    if record.target_source.clean_required:
        # Frozen template: porcelain v1 -z --untracked-files=all
        # --ignore-submodules=none. Repository .gitignore is honored; only
        # ambient sources (global config, core.excludesFile, attributes,
        # fsmonitor, untracked cache) are suppressed by the -c overrides.
        proc = _run(
            _git_argv(
                root,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ),
            root,
        )
        if proc.returncode != 0 or proc.stdout:
            raise ContractError("worktree is not clean")

    for mapping in record.mappings:
        old_bytes = _cat_blob(
            root,
            mapping.old_revision,
            mapping.old_path,
            mapping.adr_number,
            "old",
        )
        if hashlib.sha256(old_bytes).hexdigest() != mapping.old_sha256:
            raise ContractError(f"old blob digest mismatch for ADR {mapping.adr_number}")
        cur_bytes = _cat_blob(
            root,
            mapping.current_revision,
            mapping.current_path,
            mapping.adr_number,
            "current",
        )
        if hashlib.sha256(cur_bytes).hexdigest() != mapping.current_sha256:
            raise ContractError(f"current blob digest mismatch for ADR {mapping.adr_number}")

    return SourceVerification(
        observed_revision=head,
        revision_relation=revision_relation,
        mapping_count=len(record.mappings),
    )
