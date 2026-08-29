from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from ..core import ContractError
from .contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    bounded_array,
    bounded_string,
    boolean,
    identifier,
    integer,
    relative_posix_path,
    sha256_digest,
    strict_json_loads,
    strict_object,
)


ARTIFACT_MANIFEST_NAME = "artifact_manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
RESERVED_DERIVED_PATHS = frozenset({ARTIFACT_MANIFEST_NAME, CHECKSUMS_NAME})
DEFAULT_PER_FILE_BYTE_LIMIT = 64 * 1024 * 1024
DEFAULT_TOTAL_BYTE_LIMIT = 512 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactSpec:
    artifact_id: str
    path: str
    media_type: str
    schema_type: Optional[str]
    custody: str = "benchmark_measurement"
    required: bool = True


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    path: str
    media_type: str
    schema_type: Optional[str]
    custody: str
    required: bool
    ordinal: int
    present: bool
    size_bytes: Optional[int]
    sha256: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.artifact_id,
            "path": self.path,
            "media_type": self.media_type,
            "schema_type": self.schema_type,
            "custody": self.custody,
            "required": self.required,
            "ordinal": self.ordinal,
            "present": self.present,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def _root(path: Path) -> Path:
    try:
        info = path.lstat()
    except OSError as error:
        raise ContractError(f"cannot inspect artifact root {path}: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ContractError("artifact root must be a real directory, not a symlink")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ContractError(f"cannot resolve artifact root {path}: {error}") from error


def _candidate(root: Path, relative_text: str) -> Path:
    relative = relative_posix_path(relative_text, "artifact path")
    if relative in RESERVED_DERIVED_PATHS:
        raise ContractError(f"artifact path {relative!r} is reserved")
    return root.joinpath(*relative.split("/"))


def _lstat_components(root: Path, candidate: Path) -> os.stat_result:
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ContractError("artifact path escapes the artifact root") from error
    current = root
    final: Optional[os.stat_result] = None
    for component in relative.parts:
        current = current / component
        try:
            final = current.lstat()
        except OSError as error:
            raise ContractError(f"cannot inspect artifact path {current}: {error}") from error
        if stat.S_ISLNK(final.st_mode):
            raise ContractError(f"artifact path contains a symlink: {current}")
    assert final is not None
    return final


def _read_regular_file(
    root: Path, relative_text: str, per_file_byte_limit: int
) -> Tuple[int, str, Tuple[int, int]]:
    candidate = _candidate(root, relative_text)
    before = _lstat_components(root, candidate)
    if not stat.S_ISREG(before.st_mode):
        raise ContractError(f"artifact must be a regular file: {relative_text!r}")
    if before.st_nlink != 1:
        raise ContractError(f"artifact has a hard-link identity conflict: {relative_text!r}")
    if before.st_size > per_file_byte_limit:
        raise ContractError(
            f"artifact {relative_text!r} exceeds the {per_file_byte_limit}-byte file limit"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as error:
        raise ContractError(f"cannot open artifact {relative_text!r}: {error}") from error
    try:
        try:
            opened = os.fstat(fd)
        except OSError as error:
            raise ContractError(f"cannot stat artifact {relative_text!r}: {error}") from error
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ContractError(f"artifact changed while opening: {relative_text!r}")
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ContractError(f"artifact is not an unaliased regular file: {relative_text!r}")
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            try:
                chunk = os.read(fd, min(64 * 1024, remaining))
            except OSError as error:
                raise ContractError(f"cannot read artifact {relative_text!r}: {error}") from error
            if not chunk:
                raise ContractError(f"artifact was truncated while reading: {relative_text!r}")
            digest.update(chunk)
            remaining -= len(chunk)
        try:
            final = os.fstat(fd)
        except OSError as error:
            raise ContractError(f"cannot restat artifact {relative_text!r}: {error}") from error
        if (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_nlink,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        ):
            raise ContractError(f"artifact changed while reading: {relative_text!r}")
        return final.st_size, digest.hexdigest(), (final.st_dev, final.st_ino)
    finally:
        pending = sys.exc_info()[0]
        try:
            os.close(fd)
        except OSError as error:
            if pending is None:
                raise ContractError(f"cannot close artifact {relative_text!r}: {error}") from error


def _inventory(root: Path) -> Set[str]:
    paths: Set[str] = set()
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for directory, directories, files in walker:
            directory_path = Path(directory)
            for name in list(directories):
                path = directory_path / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ContractError(f"artifact root contains a symlink: {path}")
                if not stat.S_ISDIR(info.st_mode):
                    raise ContractError(f"artifact root contains a special entry: {path}")
            for name in files:
                path = directory_path / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ContractError(f"artifact root contains a symlink: {path}")
                if not stat.S_ISREG(info.st_mode):
                    raise ContractError(f"artifact root contains a special file: {path}")
                if info.st_nlink != 1:
                    raise ContractError(f"artifact root contains a hard-link conflict: {path}")
                paths.add(path.relative_to(root).as_posix())
    except ContractError:
        raise
    except OSError as error:
        raise ContractError(f"cannot inventory artifact root: {error}") from error
    return paths


def _validate_spec(spec: ArtifactSpec, ordinal: int) -> ArtifactSpec:
    identifier(spec.artifact_id, f"artifacts[{ordinal - 1}].id")
    relative_posix_path(spec.path, f"artifacts[{ordinal - 1}].path")
    if spec.path in RESERVED_DERIVED_PATHS:
        raise ContractError(f"artifact path {spec.path!r} is reserved")
    bounded_string(spec.media_type, f"artifacts[{ordinal - 1}].media_type", maximum_bytes=128)
    if spec.schema_type is not None:
        bounded_string(spec.schema_type, f"artifacts[{ordinal - 1}].schema_type", maximum_bytes=256)
    if spec.custody not in ("benchmark_measurement", "diagnostic_import"):
        raise ContractError("artifact custody must be benchmark_measurement or diagnostic_import")
    boolean(spec.required, f"artifacts[{ordinal - 1}].required")
    return spec


def build_artifact_manifest(
    root: Path,
    specs: Sequence[ArtifactSpec],
    *,
    campaign_id: str,
    per_file_byte_limit: int = DEFAULT_PER_FILE_BYTE_LIMIT,
    total_byte_limit: int = DEFAULT_TOTAL_BYTE_LIMIT,
    reject_undeclared: bool = True,
) -> Mapping[str, Any]:
    resolved = _root(root)
    campaign = identifier(campaign_id, "campaign_id")
    file_limit = integer(per_file_byte_limit, "per_file_byte_limit", minimum=1)
    total_limit = integer(total_byte_limit, "total_byte_limit", minimum=1)
    if not specs:
        raise ContractError("artifact specs must not be empty")
    if len(specs) > 4096:
        raise ContractError("artifact specs exceed the 4096-entry bound")
    records: List[ArtifactRecord] = []
    ids: Set[str] = set()
    paths: Set[str] = set()
    identities: Set[Tuple[int, int]] = set()
    total = 0
    for ordinal, spec in enumerate(specs, start=1):
        _validate_spec(spec, ordinal)
        if spec.artifact_id in ids:
            raise ContractError(f"duplicate artifact ID {spec.artifact_id!r}")
        if spec.path in paths:
            raise ContractError(f"duplicate artifact path {spec.path!r}")
        ids.add(spec.artifact_id)
        paths.add(spec.path)
        candidate = _candidate(resolved, spec.path)
        try:
            candidate.lstat()
        except FileNotFoundError:
            if spec.required:
                raise ContractError(f"required artifact is missing: {spec.path!r}")
            records.append(
                ArtifactRecord(
                    spec.artifact_id,
                    spec.path,
                    spec.media_type,
                    spec.schema_type,
                    spec.custody,
                    spec.required,
                    ordinal,
                    False,
                    None,
                    None,
                )
            )
            continue
        except OSError as error:
            raise ContractError(f"cannot inspect artifact {spec.path!r}: {error}") from error
        size, digest, identity = _read_regular_file(resolved, spec.path, file_limit)
        if identity in identities:
            raise ContractError(f"artifact aliases another declared file: {spec.path!r}")
        identities.add(identity)
        total += size
        if total > total_limit:
            raise ContractError(f"artifacts exceed the {total_limit}-byte total limit")
        records.append(
            ArtifactRecord(
                spec.artifact_id,
                spec.path,
                spec.media_type,
                spec.schema_type,
                spec.custody,
                spec.required,
                ordinal,
                True,
                size,
                digest,
            )
        )
    if reject_undeclared:
        undeclared = _inventory(resolved) - paths - RESERVED_DERIVED_PATHS
        if undeclared:
            raise ContractError(f"artifact root contains undeclared files: {sorted(undeclared)!r}")
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "campaign_id": campaign,
        "per_file_byte_limit": file_limit,
        "total_byte_limit": total_limit,
        "artifact_count": len(records),
        "present_artifact_count": sum(record.present for record in records),
        "total_size_bytes": total,
        "artifacts": [record.as_dict() for record in records],
    }


def _specs_from_manifest(value: Any) -> Tuple[Sequence[ArtifactSpec], Mapping[str, Any]]:
    obj = strict_object(
        value,
        (
            "schema_version",
            "campaign_id",
            "per_file_byte_limit",
            "total_byte_limit",
            "artifact_count",
            "present_artifact_count",
            "total_size_bytes",
            "artifacts",
        ),
        where="artifact manifest",
    )
    if obj["schema_version"] != ARTIFACT_MANIFEST_SCHEMA_VERSION:
        raise ContractError("artifact manifest schema_version is not v1")
    identifier(obj["campaign_id"], "artifact manifest.campaign_id")
    artifacts = bounded_array(obj["artifacts"], "artifact manifest.artifacts", maximum_items=4096, allow_empty=False)
    specs: List[ArtifactSpec] = []
    for index, raw in enumerate(artifacts):
        where = f"artifact manifest.artifacts[{index}]"
        entry = strict_object(
            raw,
            (
                "id",
                "path",
                "media_type",
                "schema_type",
                "custody",
                "required",
                "ordinal",
                "present",
                "size_bytes",
                "sha256",
            ),
            where=where,
        )
        if entry["ordinal"] != index + 1:
            raise ContractError(f"{where}.ordinal must preserve manifest order")
        spec = ArtifactSpec(
            entry["id"], entry["path"], entry["media_type"], entry["schema_type"], entry["custody"], entry["required"]
        )
        _validate_spec(spec, index + 1)
        present = boolean(entry["present"], f"{where}.present")
        if present:
            integer(entry["size_bytes"], f"{where}.size_bytes")
            sha256_digest(entry["sha256"], f"{where}.sha256")
        elif entry["size_bytes"] is not None or entry["sha256"] is not None or spec.required:
            raise ContractError(f"{where} has an invalid absent projection")
        specs.append(spec)
    integer(obj["artifact_count"], "artifact manifest.artifact_count")
    integer(obj["present_artifact_count"], "artifact manifest.present_artifact_count")
    integer(obj["total_size_bytes"], "artifact manifest.total_size_bytes")
    return specs, obj


def validate_artifact_manifest(
    root: Path, value: Any, *, reject_undeclared: bool = True
) -> Mapping[str, Any]:
    specs, declared = _specs_from_manifest(value)
    recomputed = build_artifact_manifest(
        root,
        specs,
        campaign_id=declared["campaign_id"],
        per_file_byte_limit=integer(
            declared["per_file_byte_limit"], "artifact manifest.per_file_byte_limit", minimum=1
        ),
        total_byte_limit=integer(
            declared["total_byte_limit"], "artifact manifest.total_byte_limit", minimum=1
        ),
        reject_undeclared=reject_undeclared,
    )
    if recomputed != declared:
        raise ContractError("artifact manifest does not match governed files")
    return recomputed


def canonical_manifest_bytes(value: Any) -> bytes:
    _specs_from_manifest(value)
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"


def write_artifact_manifest(root: Path, value: Any) -> Path:
    resolved = _root(root)
    validate_artifact_manifest(resolved, value)
    path = resolved / ARTIFACT_MANIFEST_NAME
    try:
        with path.open("xb") as stream:
            stream.write(canonical_manifest_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ContractError("artifact manifest is create-only") from error
    except OSError as error:
        raise ContractError(f"cannot write artifact manifest: {error}") from error
    return path


def load_artifact_manifest(root: Path) -> Mapping[str, Any]:
    resolved = _root(root)
    path = resolved / ARTIFACT_MANIFEST_NAME
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot read artifact manifest: {error}") from error
    if len(raw) > 4 * 1024 * 1024:
        raise ContractError("artifact manifest exceeds the 4 MiB bound")
    value = strict_json_loads(raw, "artifact manifest")
    return validate_artifact_manifest(resolved, value)


def checksums_bytes(value: Any) -> bytes:
    _specs, manifest = _specs_from_manifest(value)
    entries = [entry for entry in manifest["artifacts"] if entry["present"]]
    entries.sort(key=lambda entry: entry["path"])
    return "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries).encode("utf-8")


def write_sha256s_from_manifest(root: Path, value: Any) -> Path:
    resolved = _root(root)
    validate_artifact_manifest(resolved, value)
    path = resolved / CHECKSUMS_NAME
    try:
        with path.open("xb") as stream:
            stream.write(checksums_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ContractError("SHA256SUMS is create-only") from error
    except OSError as error:
        raise ContractError(f"cannot write SHA256SUMS: {error}") from error
    return path
