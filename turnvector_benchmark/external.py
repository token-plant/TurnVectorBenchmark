from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .core import ContractError, IDENTIFIER_RE, canonical_json
from .evidence import sha256_file


EXTERNAL_FIXTURES_SCHEMA = "turnvector.benchmark.external-fixtures.v1"


@dataclass(frozen=True)
class ExternalArtifact:
    artifact_id: str
    path: Path
    kind: str
    size: int
    sha256: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.artifact_id,
            "path": str(self.path),
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ExternalFixtureManifest:
    manifest_id: str
    lock_sha256: str
    artifacts: Mapping[str, ExternalArtifact]
    source_path: Path


def _directory_identity(root: Path) -> Tuple[int, str]:
    records: List[Mapping[str, Any]] = []
    total_size = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            raise ContractError(f"external fixture directory contains a symlink: {path}")
        if stat.S_ISDIR(mode):
            records.append({"path": relative, "kind": "directory"})
            continue
        if not stat.S_ISREG(mode):
            raise ContractError(f"external fixture directory contains a special file: {path}")
        size = path.stat().st_size
        total_size += size
        records.append(
            {
                "path": relative,
                "kind": "file",
                "size": size,
                "sha256": sha256_file(path),
                "executable": bool(mode & 0o111),
            }
        )
    digest = hashlib.sha256(canonical_json(records).encode("utf-8")).hexdigest()
    return total_size, digest


def load_external_fixture_manifest(
    path: Path, *, reference_lock: Path
) -> ExternalFixtureManifest:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read external fixture manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"external fixture manifest {path} must be an object")
    expected_keys = {"schema_version", "id", "reference_lock_sha256", "artifacts"}
    if set(value) != expected_keys:
        raise ContractError(
            f"external fixture manifest fields differ: "
            f"missing={sorted(expected_keys - set(value))!r}, "
            f"unknown={sorted(set(value) - expected_keys)!r}"
        )
    if value["schema_version"] != EXTERNAL_FIXTURES_SCHEMA:
        raise ContractError(
            f"external fixture schema must be {EXTERNAL_FIXTURES_SCHEMA!r}"
        )
    manifest_id = value["id"]
    if not isinstance(manifest_id, str) or not IDENTIFIER_RE.fullmatch(manifest_id):
        raise ContractError("external fixture manifest id is invalid")
    expected_lock = sha256_file(reference_lock)
    if value["reference_lock_sha256"] != expected_lock:
        raise ContractError(
            "external fixture manifest was not prepared for the checked-in reference lock"
        )
    raw_artifacts = value["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ContractError("external fixture artifacts must be a non-empty array")
    artifacts: Dict[str, ExternalArtifact] = {}
    for index, raw in enumerate(raw_artifacts):
        where = f"external fixture artifacts[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"id", "path", "kind", "size", "sha256"}:
            raise ContractError(f"{where} has invalid fields")
        artifact_id = raw["id"]
        if not isinstance(artifact_id, str) or not IDENTIFIER_RE.fullmatch(artifact_id):
            raise ContractError(f"{where}.id is invalid")
        if artifact_id in artifacts:
            raise ContractError(f"external fixture artifact id {artifact_id!r} is duplicated")
        artifact_path = Path(raw["path"])
        if not artifact_path.is_absolute():
            artifact_path = path.parent / artifact_path
        try:
            artifact_mode = os.lstat(artifact_path).st_mode
        except OSError as error:
            raise ContractError(f"{where}.path cannot be inspected: {error}") from error
        if stat.S_ISLNK(artifact_mode):
            raise ContractError(f"{where}.path must not be a symlink: {artifact_path}")
        artifact_path = artifact_path.resolve()
        kind = raw["kind"]
        if kind not in {"file", "directory"}:
            raise ContractError(f"{where}.kind must be file or directory")
        if isinstance(raw["size"], bool) or not isinstance(raw["size"], int) or raw["size"] < 0:
            raise ContractError(f"{where}.size must be a non-negative integer")
        digest = raw["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ContractError(f"{where}.sha256 must be a lowercase SHA256")
        if kind == "file":
            if not artifact_path.is_file():
                raise ContractError(f"{where}.path is not a file: {artifact_path}")
            observed_size = artifact_path.stat().st_size
            observed_digest = sha256_file(artifact_path)
        else:
            if not artifact_path.is_dir():
                raise ContractError(f"{where}.path is not a directory: {artifact_path}")
            observed_size, observed_digest = _directory_identity(artifact_path)
        if raw["size"] != observed_size or digest != observed_digest:
            raise ContractError(
                f"{where} identity mismatch: declared size/hash={raw['size']}/{digest}, "
                f"observed={observed_size}/{observed_digest}"
            )
        artifacts[artifact_id] = ExternalArtifact(
            artifact_id=artifact_id,
            path=artifact_path,
            kind=kind,
            size=observed_size,
            sha256=observed_digest,
        )
    return ExternalFixtureManifest(
        manifest_id=manifest_id,
        lock_sha256=expected_lock,
        artifacts=artifacts,
        source_path=path.resolve(),
    )
