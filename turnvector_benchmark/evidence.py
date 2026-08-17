from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .core import ContractError


CONTROLLER_ARTIFACT_IDS = {"manifest", "environment", "report", "checksums"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def git_identity(path: Path) -> Dict[str, Any]:
    resolved = path.resolve()
    try:
        root = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", root, "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ContractError(f"cannot inspect Git repository {resolved}: {error}") from error
    return {
        "path": root,
        "head": head,
        "status_short": status,
        "dirty": bool(status),
    }


def git_evidence_still_valid(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return (
        before.get("path") == after.get("path")
        and before.get("head") == after.get("head")
        and before.get("status_short") == after.get("status_short")
    )


def validate_binary_manifest(
    entries: Sequence[Mapping[str, str]], adapter_cwd: Path
) -> List[Dict[str, Any]]:
    verified: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries):
        candidate = Path(entry["path"])
        if not candidate.is_absolute():
            candidate = adapter_cwd / candidate
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise ContractError(f"subject binary_manifest[{index}] is not a file: {resolved}")
        observed = sha256_file(resolved)
        if observed != entry["sha256"]:
            raise ContractError(
                f"subject binary_manifest[{index}] hash mismatch: "
                f"declared={entry['sha256']}, observed={observed}"
            )
        verified.append(
            {"path": str(resolved), "sha256": observed, "size": resolved.stat().st_size}
        )
    return verified


def _ensure_no_symlink(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for component in relative.parts:
        current = current / component
        try:
            mode = os.lstat(current).st_mode
        except OSError as error:
            raise ContractError(f"cannot inspect subject artifact path {current}: {error}") from error
        if stat.S_ISLNK(mode):
            raise ContractError(f"subject artifact path contains a symlink: {current}")


def validate_subject_artifact(
    descriptor: Mapping[str, Any], artifact_root: Path
) -> Dict[str, Any]:
    artifact_id = descriptor.get("id")
    relative_text = descriptor.get("path")
    size = descriptor.get("size")
    digest = descriptor.get("sha256")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ContractError("subject artifact id must be a non-empty string")
    if not isinstance(relative_text, str) or not relative_text:
        raise ContractError("subject artifact path must be a non-empty relative path")
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ContractError(f"subject artifact path escapes artifact root: {relative_text!r}")
    root = artifact_root.resolve()
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ContractError(f"subject artifact does not exist: {relative_text!r}: {error}") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ContractError(f"subject artifact path escapes artifact root: {relative_text!r}") from error
    _ensure_no_symlink(root, candidate)
    if not resolved.is_file():
        raise ContractError(f"subject artifact must be a regular file: {relative_text!r}")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ContractError("subject artifact size must be a non-negative integer")
    observed_size = resolved.stat().st_size
    if size != observed_size:
        raise ContractError(
            f"subject artifact size mismatch for {relative_text!r}: "
            f"declared={size}, observed={observed_size}"
        )
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ContractError("subject artifact sha256 must be a lowercase SHA256")
    observed_digest = sha256_file(resolved)
    if digest != observed_digest:
        raise ContractError(
            f"subject artifact hash mismatch for {relative_text!r}: "
            f"declared={digest}, observed={observed_digest}"
        )
    return {
        "id": artifact_id,
        "path": relative_text,
        "size": observed_size,
        "sha256": observed_digest,
    }


def write_checksums(root: Path, *, exclude: Sequence[str] = ("SHA256SUMS",)) -> Path:
    excluded = set(exclude)
    entries: List[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        entries.append(f"{sha256_file(path)}  {relative}")
    output = root / "SHA256SUMS"
    output.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return output
