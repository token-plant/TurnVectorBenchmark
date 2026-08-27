"""Tests for verify_source_reconciliation against a real local Git repository."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import turnvector_benchmark.source_verification as source_verification
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.source_reconciliation import load_source_reconciliation
from turnvector_benchmark.source_verification import verify_source_reconciliation

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "authority" / "source-reconciliation-v1.json"

SECRET = b"DISTINCTIVE-BLOB-SECRET-7f3c9a11"

# Production frozen constants, captured before tests patch the module so their
# exact strings can still be asserted on any runner.
_PRODUCTION_QUALIFIED_GIT = source_verification.QUALIFIED_GIT
_PRODUCTION_GIT_EXEC_PATH = source_verification.GIT_EXEC_PATH

# CI runs on ubuntu-latest where the frozen Xcode paths do not exist, so
# behavior tests inject the runner's own git executable and exec path while
# the production constants keep their frozen exact values.
_RUNNER_GIT = shutil.which("git") or "git"
_RUNNER_GIT_EXEC_PATH = subprocess.run(
    ["git", "--exec-path"], capture_output=True, check=True, text=True
).stdout.strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _old_blob_bytes(mapping, index):
    data = f"old:{index}:{mapping.old_path}".encode("utf-8")
    if index == 0:
        data += b":" + SECRET
    return data


def _current_blob_bytes(mapping, index):
    return f"current:{index}:{mapping.current_path}".encode("utf-8")


class SourceReconciliationVerificationTests(unittest.TestCase):
    def setUp(self):
        self._patch_git = mock.patch.object(
            source_verification, "QUALIFIED_GIT", _RUNNER_GIT
        )
        self._patch_exec_path = mock.patch.object(
            source_verification, "GIT_EXEC_PATH", _RUNNER_GIT_EXEC_PATH
        )
        self._patch_git.start()
        self._patch_exec_path.start()
        self.addCleanup(self._patch_git.stop)
        self.addCleanup(self._patch_exec_path.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "TurnVector"
        self.root.mkdir()
        self._git("init")
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.com")

        record = load_source_reconciliation(ARTIFACT)

        for index, mapping in enumerate(record.mappings):
            path = self.root / mapping.old_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_old_blob_bytes(mapping, index))
        self._git("add", "-A")
        self._git("commit", "-m", "old")
        self.old_head = self._git("rev-parse", "HEAD")

        self._git("rm", "-rf", "--ignore-unmatch", ".")
        for index, mapping in enumerate(record.mappings):
            path = self.root / mapping.current_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_current_blob_bytes(mapping, index))
        self._git("add", "-A")
        self._git("commit", "-m", "current")
        self.current_head = self._git("rev-parse", "HEAD")

        predecessor = dataclasses.replace(
            record.predecessor_expectation, source_revision=self.old_head
        )
        target = dataclasses.replace(record.target_source, revision=self.current_head)
        mappings = []
        for index, mapping in enumerate(record.mappings):
            mappings.append(
                dataclasses.replace(
                    mapping,
                    old_revision=self.old_head,
                    current_revision=self.current_head,
                    old_sha256=_sha256(_old_blob_bytes(mapping, index)),
                    current_sha256=_sha256(_current_blob_bytes(mapping, index)),
                )
            )
        self.record = dataclasses.replace(
            record,
            predecessor_expectation=predecessor,
            target_source=target,
            mappings=tuple(mappings),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            check=True,
            cwd=self.root,
        ).stdout.decode("utf-8").strip()

    def _replace_first(self, **field_updates):
        first = dataclasses.replace(self.record.mappings[0], **field_updates)
        mappings = (first,) + self.record.mappings[1:]
        return dataclasses.replace(self.record, mappings=mappings)

    def test_exact_verification(self):
        result = verify_source_reconciliation(self.record, self.root)
        self.assertEqual(result.observed_revision, self.current_head)
        self.assertEqual(result.revision_relation, "exact")
        self.assertEqual(result.mapping_count, 7)

    def test_descendant_allow_empty_commit(self):
        self._git("commit", "--allow-empty", "-m", "descendant")
        descendant_head = self._git("rev-parse", "HEAD")
        result = verify_source_reconciliation(self.record, self.root)
        self.assertEqual(result.observed_revision, descendant_head)
        self.assertEqual(result.revision_relation, "descendant")
        self.assertEqual(result.mapping_count, 7)

    def test_untracked_file_is_not_clean(self):
        (self.root / "untracked-file.txt").write_bytes(b"untracked")
        with self.assertRaisesRegex(ContractError, "clean"):
            verify_source_reconciliation(self.record, self.root)

    def test_repository_ignored_file_is_tolerated(self):
        # The frozen status template (porcelain v1 -z --untracked-files=all
        # --ignore-submodules=none) honors the repository .gitignore, so a
        # repo-ignored untracked file is not a cleanliness violation.
        (self.root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "gitignore")
        (self.root / "hidden.tmp").write_bytes(b"hidden")
        result = verify_source_reconciliation(self.record, self.root)
        self.assertEqual(result.revision_relation, "descendant")
        self.assertEqual(result.mapping_count, 7)

    def test_tracked_dirty_file_is_not_clean(self):
        path = self.root / self.record.mappings[0].current_path
        path.write_bytes(path.read_bytes() + b"dirty")
        with self.assertRaisesRegex(ContractError, "clean"):
            verify_source_reconciliation(self.record, self.root)

    def test_ambient_global_config_isolation(self):
        with tempfile.TemporaryDirectory() as home:
            excludes = Path(home) / "excludes"
            excludes.write_text("*\n", encoding="utf-8")
            gitconfig = Path(home) / ".gitconfig"
            gitconfig.write_text(
                f"[core]\n\texcludesFile = {excludes}\n", encoding="utf-8"
            )
            (self.root / "untracked.txt").write_bytes(b"untracked")
            with mock.patch.dict(os.environ, {"HOME": home}):
                with self.assertRaisesRegex(ContractError, "clean"):
                    verify_source_reconciliation(self.record, self.root)

    def test_ambient_git_state_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as evil:
            bogus = str(Path(evil) / "bogus")
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": bogus,
                    "GIT_WORK_TREE": bogus,
                    "GIT_INDEX_FILE": bogus,
                    "GIT_OBJECT_DIRECTORY": bogus,
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": bogus,
                    "GIT_NAMESPACE": "refs/namespaces/evil",
                    "GIT_CEILING_DIRECTORIES": bogus,
                    "GIT_GRAFT_FILE": bogus,
                    "GIT_SHALLOW_FILE": bogus,
                    "GIT_CONFIG_GLOBAL": bogus,
                    "GIT_CONFIG_SYSTEM": bogus,
                    "GIT_ATTR_NOSYSTEM": "0",
                    "GIT_NO_REPLACE_OBJECTS": "0",
                    "GIT_OPTIONAL_LOCKS": "1",
                    "GIT_TERMINAL_PROMPT": "1",
                    "GIT_ASKPASS": bogus,
                    "SSH_ASKPASS": bogus,
                },
            ):
                result = verify_source_reconciliation(self.record, self.root)
        self.assertEqual(result.revision_relation, "exact")
        self.assertEqual(result.mapping_count, 7)

    def test_replace_objects_are_not_followed(self):
        mapping = self.record.mappings[0]
        old_sha = self._git("rev-parse", f"{self.old_head}:{mapping.old_path}")
        replacement = self.root / "replacement-blob"
        replacement.write_bytes(b"replacement content that must never be read")
        replacement_sha = self._git("hash-object", "-w", str(replacement))
        replacement.unlink()
        self._git("replace", old_sha, replacement_sha)
        result = verify_source_reconciliation(self.record, self.root)
        self.assertEqual(result.mapping_count, 7)

    def test_qualified_git_executable_is_frozen(self):
        self.assertEqual(
            _PRODUCTION_QUALIFIED_GIT,
            "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/bin/git",
        )
        self.assertEqual(
            _PRODUCTION_GIT_EXEC_PATH,
            "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core",
        )
        # The Xcode filesystem paths are macOS-only; ubuntu-latest CI must not
        # require them, but a qualified Mac still verifies the binary exists.
        if os.path.exists(_PRODUCTION_QUALIFIED_GIT):
            self.assertTrue(os.path.isfile(_PRODUCTION_QUALIFIED_GIT))

    def test_git_timeout_is_bounded_contract_error(self):
        fake = self.base / "fake-git"
        fake.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.object(
            source_verification, "QUALIFIED_GIT", str(fake)
        ), mock.patch.object(source_verification, "GIT_TIMEOUT_SECONDS", 0.2):
            with self.assertRaisesRegex(ContractError, "timed out"):
                verify_source_reconciliation(self.record, self.root)

    def test_closed_pipes_then_sleep_times_out_promptly(self):
        # Regression: a child that closes both output streams but keeps
        # running (here a 3-second sleep) must still be bounded by the same
        # deadline as pipe draining. The old drain loop exited as soon as
        # both pipes hit EOF and then waited on the child without any
        # remaining-deadline timeout, so the invocation returned only after
        # the full sleep instead of timing out promptly.
        done_marker = self.base / "fake-git-done"
        fake = self.base / "fake-git"
        # Directly executable Python script, not a shell that forks an
        # external `sleep`: closing fds 1 and 2 and sleeping happen in this
        # one process, so killing it at the deadline leaves no descendant
        # process behind. The shebang uses the current test interpreter
        # (sys.executable), with a safe fallback when that path cannot be
        # used directly in a kernel shebang (spaces or over-long line).
        interpreter = Path(sys.executable).resolve()
        shebang = f"#!{interpreter}"
        if len(shebang) > 127 or any(ch.isspace() for ch in str(interpreter)):
            # Point the shebang at a short, space-free symlink to the same
            # interpreter instead of an unusable direct path.
            interpreter_link = self.base / "current-test-interpreter"
            interpreter_link.symlink_to(interpreter)
            shebang = f"#!{interpreter_link}"
            if len(shebang) > 127 or any(
                ch.isspace() for ch in str(interpreter_link)
            ):
                shebang = "#!/usr/bin/env python3"
        fake.write_text(
            shebang + "\n"
            "import os\n"
            "import time\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(3)\n"
            f"open({str(done_marker)!r}, 'w').write('done')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        started = time.monotonic()
        with mock.patch.object(
            source_verification, "QUALIFIED_GIT", str(fake)
        ), mock.patch.object(source_verification, "GIT_TIMEOUT_SECONDS", 0.2):
            with self.assertRaisesRegex(ContractError, "timed out"):
                verify_source_reconciliation(self.record, self.root)
        elapsed = time.monotonic() - started
        # Generous bound for CI: the 0.2s deadline plus scheduling noise must
        # stay far below the child's 3s sleep, which the buggy implementation
        # waited out in full.
        self.assertLess(elapsed, 1.5)
        # The child is killed at the deadline and never completes, so its
        # completion marker must not exist.
        self.assertFalse(done_marker.exists())

    def test_git_stdout_cap_is_bounded_contract_error(self):
        fake = self.base / "fake-git"
        fake.write_text(
            "#!/bin/sh\n"
            "dd if=/dev/zero bs=200 count=1 2>/dev/null | tr '\\000' 'x'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        with mock.patch.object(
            source_verification, "QUALIFIED_GIT", str(fake)
        ), mock.patch.object(source_verification, "GIT_STDOUT_BYTES_MAX", 64):
            with self.assertRaisesRegex(ContractError, "over-bounded"):
                verify_source_reconciliation(self.record, self.root)

    def test_output_producing_process_is_stopped_at_cap(self):
        # The child writes far more than the cap (past any pipe buffer), then
        # would touch a completion marker if it ever finished. The drain must
        # kill it while it is still producing output, so the marker is never
        # created -- proving the cap is enforced against live output rather
        # than merely checked after completion.
        done_marker = self.base / "fake-git-done"
        fake = self.base / "fake-git"
        fake.write_text(
            "#!/bin/sh\n"
            "i=0\n"
            "while [ \"$i\" -lt 30000 ]; do\n"
            "  printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'\n"
            "  i=$((i+1))\n"
            "done\n"
            f"touch '{done_marker}'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        with mock.patch.object(
            source_verification, "QUALIFIED_GIT", str(fake)
        ), mock.patch.object(source_verification, "GIT_STDOUT_BYTES_MAX", 64):
            with self.assertRaisesRegex(ContractError, "over-bounded"):
                verify_source_reconciliation(self.record, self.root)
        self.assertFalse(done_marker.exists())

    def test_git_stderr_is_not_leaked(self):
        fake = self.base / "fake-git"
        fake.write_text(
            "#!/bin/sh\nprintf 'LEAK-SECRET-9f2c1d\\n' >&2\nexit 1\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        with mock.patch.object(source_verification, "QUALIFIED_GIT", str(fake)):
            with self.assertRaisesRegex(
                ContractError, "rev-parse --show-toplevel failed"
            ) as cm:
                verify_source_reconciliation(self.record, self.root)
        self.assertNotIn("LEAK-SECRET-9f2c1d", str(cm.exception))

    def test_unrelated_orphan_commit(self):
        self._git("checkout", "--orphan", "orphan-branch")
        self._git("rm", "-rf", "--ignore-unmatch", ".")
        (self.root / "unrelated.txt").write_bytes(b"unrelated")
        self._git("add", "-A")
        self._git("commit", "-m", "unrelated")
        with self.assertRaisesRegex(ContractError, "unrelated"):
            verify_source_reconciliation(self.record, self.root)

    def test_digest_mismatch_does_not_leak_blob(self):
        record = self._replace_first(old_sha256="0" * 64)
        with self.assertRaisesRegex(ContractError, "digest mismatch") as cm:
            verify_source_reconciliation(record, self.root)
        self.assertNotIn(SECRET.decode("utf-8"), str(cm.exception))

    def test_missing_old_blob(self):
        record = self._replace_first(old_path="docs/adr/0001-does-not-exist.md")
        with self.assertRaisesRegex(ContractError, "missing old blob"):
            verify_source_reconciliation(record, self.root)

    def test_current_blob_digest_mismatch(self):
        record = self._replace_first(current_sha256="0" * 64)
        with self.assertRaisesRegex(ContractError, "current blob digest mismatch"):
            verify_source_reconciliation(record, self.root)

    def test_missing_current_blob(self):
        record = self._replace_first(current_path="docs/adr/0001-not-in-current.md")
        with self.assertRaisesRegex(ContractError, "missing current blob"):
            verify_source_reconciliation(record, self.root)

    def test_wrong_root_subdirectory(self):
        subdir = self.root / "docs"
        with self.assertRaisesRegex(ContractError, "does not resolve"):
            verify_source_reconciliation(self.record, subdir)

    def test_frozen_child_environment_matches_design_document(self):
        # The module constant must stay byte-for-byte equal to the frozen
        # 12-key Sanitized Child Environment in docs/D0-AUTHORITY-DESIGN.md.
        self.assertEqual(
            source_verification._GIT_CHILD_ENV,
            {
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_EXEC_PATH": "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core:/usr/bin:/bin",
                "TZ": "UTC",
            },
        )

    def test_child_environment_is_exact_frozen_set(self):
        # The exact environment handed to Popen must be the frozen 12-key set
        # (GIT_EXEC_PATH from the patched module constant), never an
        # inheritance of the caller environment: every ambient GIT_*, locale,
        # HOME, DEVELOPER_DIR, DYLD_*, PYTHON*, XDG_*, and SSH_* variable must
        # be absent from every child invocation.
        real_popen = subprocess.Popen
        seen = []

        def _capturing_popen(*args, **kwargs):
            seen.append(dict(kwargs["env"]))
            return real_popen(*args, **kwargs)

        with mock.patch.dict(
            os.environ,
            {
                "DEVELOPER_DIR": "/evil",
                "DYLD_INSERT_LIBRARIES": "/evil",
                "PYTHONPATH": "/evil",
                "XDG_CONFIG_HOME": "/evil",
                "SSH_ASKPASS": "/evil",
                "GIT_DIR": "/evil",
                "GIT_WORK_TREE": "/evil",
                "GIT_INDEX_FILE": "/evil",
                "GIT_TRACE": "1",
                "GIT_CONFIG_GLOBAL": "/evil",
                "GIT_TERMINAL_PROMPT": "1",
                "LANG": "fr_FR.UTF-8",
                "LC_ALL": "fr_FR.UTF-8",
                "TZ": "America/New_York",
                "HOME": "/evil",
            },
        ), mock.patch.object(
            source_verification.subprocess, "Popen", _capturing_popen
        ):
            result = verify_source_reconciliation(self.record, self.root)
        self.assertEqual(result.mapping_count, 7)
        self.assertTrue(seen)
        for child_env in seen:
            self.assertEqual(
                child_env,
                {
                    "GIT_ATTR_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_EXEC_PATH": _RUNNER_GIT_EXEC_PATH,
                    "GIT_NO_REPLACE_OBJECTS": "1",
                    "GIT_OPTIONAL_LOCKS": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                    "HOME": "/var/empty",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core:/usr/bin:/bin",
                    "TZ": "UTC",
                },
            )

    def test_unresolvable_target_root_is_contract_error(self):
        # A self-referential symlink makes Path.resolve raise (OSError on
        # Python 3.13+, RuntimeError before); it must become a fixed bounded
        # ContractError instead of leaking a raw exception.
        loop = self.base / "root-loop"
        loop.symlink_to(loop)
        with self.assertRaisesRegex(ContractError, "cannot resolve target repository"):
            verify_source_reconciliation(self.record, loop)

    def test_unresolvable_git_toplevel_is_contract_error(self):
        real_resolve = Path.resolve
        calls = {"count": 0}

        def _flaky_resolve(self_ref, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise RuntimeError("symlink loop")
            return real_resolve(self_ref, *args, **kwargs)

        with mock.patch.object(source_verification.Path, "resolve", _flaky_resolve):
            with self.assertRaisesRegex(ContractError, "cannot resolve git toplevel"):
                verify_source_reconciliation(self.record, self.root)


if __name__ == "__main__":
    unittest.main()
