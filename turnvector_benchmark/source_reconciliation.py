from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .core import ContractError, IDENTIFIER_RE


SOURCE_RECONCILIATION_SCHEMA = "turnvector.benchmark.source-reconciliation.v1"
AUTHORITY_FILE_BYTES_MAX = 4 * 1024 * 1024  # authority_file_bytes_max
SOURCE_RECONCILIATION_ID = "source-reconciliation-v1"
SOURCE_RECONCILIATION_V1_SHA256 = (
    "13706cdd46416d394fe3b8c9ce47203c7b77367199be31f502eb3b7122db7a1c"
)
PREDECESSOR_EXPECTATION_ID = "turnvector-implementation-v1"
SUCCESSOR_EXPECTATION_ID = "turnvector-implementation-v2"
SUCCESSOR_EXPECTATION_SCHEMA = "turnvector.benchmark.expectation.v3"
TARGET_REPOSITORY = "TurnVector"
EXACT_ADR_NUMBERS = ("0001", "0018", "0019", "0020", "0029", "0030", "0035")
EXACT_LANE_TUPLES = {
    "0001": ("cross-model-serving", "mlx-native-correctness"),
    "0018": ("bounded-turn-and-ffi", "mlx-native-correctness", "certification-envelopes"),
    "0019": (
        "observability-qualification",
        "bounded-turn-and-ffi",
        "mlx-native-correctness",
        "protocol-and-owner-lifecycle",
        "certification-envelopes",
    ),
    "0020": ("mlx-native-correctness", "protocol-and-owner-lifecycle", "certification-envelopes"),
    "0029": ("protocol-and-owner-lifecycle",),
    "0030": ("request-serving-lifecycle", "protocol-and-owner-lifecycle"),
    "0035": ("protocol-and-owner-lifecycle",),
}
ADR_RE = re.compile(r"^[0-9]{4}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POSIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
CLASSIFICATION_DISPOSITION_PAIRS = {
    ("material", "replace_topology_obligations"),
    ("scope_clarification", "retain_with_scope_update"),
}


def _object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _strict_keys(value: Mapping[str, Any], required: Tuple[str, ...], where: str) -> None:
    required_set = set(required)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - required_set)
    if missing:
        raise ContractError(f"{where} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{where} has unknown fields: {', '.join(unknown)}")


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be a non-empty string")
    return value


def _identifier(value: Any, where: str) -> str:
    text = _string(value, where)
    if not IDENTIFIER_RE.fullmatch(text):
        raise ContractError(f"{where} must match {IDENTIFIER_RE.pattern}")
    return text


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where} must be a boolean")
    return value


def _revision(value: Any, where: str) -> str:
    text = _string(value, where)
    if not REVISION_RE.fullmatch(text):
        raise ContractError(f"{where} must be a lowercase 40-character Git SHA")
    return text


def _sha256(value: Any, where: str) -> str:
    text = _string(value, where)
    if not SHA256_RE.fullmatch(text):
        raise ContractError(f"{where} must be a lowercase 64-character SHA-256 digest")
    return text


def _posix_path(value: Any, where: str) -> str:
    text = _string(value, where)
    if "\\" in text or text.startswith("/") or "//" in text:
        raise ContractError(f"{where} must be a normalized repository-relative POSIX path")
    for component in text.split("/"):
        if component in ("", ".", "..") or not POSIX_COMPONENT_RE.fullmatch(component):
            raise ContractError(f"{where} must be a normalized repository-relative POSIX path")
    return text


def _summary(value: Any, where: str) -> str:
    text = _string(value, where)
    if len(text) > 1024:
        raise ContractError(f"{where} must be at most 1024 characters")
    return text


@dataclass(frozen=True)
class PredecessorExpectation:
    id: str
    source_revision: str
    exact_file_sha256: str


@dataclass(frozen=True)
class SuccessorExpectation:
    id: str
    schema_version: str


@dataclass(frozen=True)
class TargetSource:
    repository: str
    revision: str
    clean_required: bool


@dataclass(frozen=True)
class SourceMapping:
    adr_number: str
    old_revision: str
    old_path: str
    old_sha256: str
    current_revision: str
    current_path: str
    current_sha256: str
    classification: str
    affected_lane_ids: Tuple[str, ...]
    disposition: str
    summary: str


@dataclass(frozen=True)
class SourceReconciliation:
    schema_version: str
    id: str
    predecessor_expectation: PredecessorExpectation
    successor_expectation: SuccessorExpectation
    target_source: TargetSource
    mappings: Tuple[SourceMapping, ...]
    design_gate_revision: str
    source_path: Path


def _parse_predecessor_expectation(value: Any, where: str) -> PredecessorExpectation:
    obj = _object(value, where)
    _strict_keys(obj, ("id", "source_revision", "exact_file_sha256"), where)
    expectation_id = _identifier(obj["id"], f"{where}.id")
    if expectation_id != PREDECESSOR_EXPECTATION_ID:
        raise ContractError(f"{where}.id must equal {PREDECESSOR_EXPECTATION_ID!r}")
    return PredecessorExpectation(
        id=expectation_id,
        source_revision=_revision(obj["source_revision"], f"{where}.source_revision"),
        exact_file_sha256=_sha256(obj["exact_file_sha256"], f"{where}.exact_file_sha256"),
    )


def _parse_successor_expectation(value: Any, where: str) -> SuccessorExpectation:
    obj = _object(value, where)
    _strict_keys(obj, ("id", "schema_version"), where)
    expectation_id = _identifier(obj["id"], f"{where}.id")
    if expectation_id != SUCCESSOR_EXPECTATION_ID:
        raise ContractError(f"{where}.id must equal {SUCCESSOR_EXPECTATION_ID!r}")
    schema_version = _string(obj["schema_version"], f"{where}.schema_version")
    if schema_version != SUCCESSOR_EXPECTATION_SCHEMA:
        raise ContractError(f"{where}.schema_version must equal {SUCCESSOR_EXPECTATION_SCHEMA!r}")
    return SuccessorExpectation(id=expectation_id, schema_version=schema_version)


def _parse_target_source(value: Any, where: str) -> TargetSource:
    obj = _object(value, where)
    _strict_keys(obj, ("repository", "revision", "clean_required"), where)
    repository = _string(obj["repository"], f"{where}.repository")
    if repository != TARGET_REPOSITORY:
        raise ContractError(f"{where}.repository must equal {TARGET_REPOSITORY!r}")
    clean_required = _boolean(obj["clean_required"], f"{where}.clean_required")
    if not clean_required:
        raise ContractError(f"{where}.clean_required must be exactly true")
    return TargetSource(
        repository=repository,
        revision=_revision(obj["revision"], f"{where}.revision"),
        clean_required=clean_required,
    )


def _parse_mapping(
    value: Any, where: str, predecessor_revision: str, target_revision: str
) -> SourceMapping:
    obj = _object(value, where)
    _strict_keys(
        obj,
        (
            "adr_number",
            "old_revision",
            "old_path",
            "old_sha256",
            "current_revision",
            "current_path",
            "current_sha256",
            "classification",
            "affected_lane_ids",
            "disposition",
            "summary",
        ),
        where,
    )
    adr_number = _string(obj["adr_number"], f"{where}.adr_number")
    if not ADR_RE.fullmatch(adr_number):
        raise ContractError(f"{where}.adr_number must be a four-digit ADR number")
    if adr_number not in EXACT_LANE_TUPLES:
        raise ContractError(f"{where}.adr_number is not in the exact reconciled ADR set")
    old_revision = _revision(obj["old_revision"], f"{where}.old_revision")
    if old_revision != predecessor_revision:
        raise ContractError(f"{where}.old_revision must equal the predecessor source_revision")
    current_revision = _revision(obj["current_revision"], f"{where}.current_revision")
    if current_revision != target_revision:
        raise ContractError(f"{where}.current_revision must equal the target revision")
    classification = _string(obj["classification"], f"{where}.classification")
    disposition = _string(obj["disposition"], f"{where}.disposition")
    if (classification, disposition) not in CLASSIFICATION_DISPOSITION_PAIRS:
        raise ContractError(
            f"{where}.classification and disposition must be a valid paired enum"
        )
    expected_pair = (
        ("scope_clarification", "retain_with_scope_update")
        if adr_number == "0030"
        else ("material", "replace_topology_obligations")
    )
    if (classification, disposition) != expected_pair:
        raise ContractError(
            f"{where}.classification and disposition do not match ADR {adr_number}"
        )
    lane_ids = tuple(
        _identifier(item, f"{where}.affected_lane_ids[{index}]")
        for index, item in enumerate(_array(obj["affected_lane_ids"], f"{where}.affected_lane_ids"))
    )
    if not lane_ids:
        raise ContractError(f"{where}.affected_lane_ids must not be empty")
    if len(lane_ids) != len(set(lane_ids)):
        raise ContractError(f"{where}.affected_lane_ids must not contain duplicates")
    if lane_ids != EXACT_LANE_TUPLES[adr_number]:
        raise ContractError(
            f"{where}.affected_lane_ids must equal the exact lane tuple for ADR {adr_number}"
        )
    return SourceMapping(
        adr_number=adr_number,
        old_revision=old_revision,
        old_path=_posix_path(obj["old_path"], f"{where}.old_path"),
        old_sha256=_sha256(obj["old_sha256"], f"{where}.old_sha256"),
        current_revision=current_revision,
        current_path=_posix_path(obj["current_path"], f"{where}.current_path"),
        current_sha256=_sha256(obj["current_sha256"], f"{where}.current_sha256"),
        classification=classification,
        affected_lane_ids=lane_ids,
        disposition=disposition,
        summary=_summary(obj["summary"], f"{where}.summary"),
    )


def _read_no_follow_regular(path: Path) -> bytes:
    """Read a strict regular file without following or blocking on it.

    The final component is lstat'ed before open and opened with O_NOFOLLOW
    where the platform provides it (macOS does), so a symlink or any
    non-regular type (directory, FIFO, device, socket) is rejected from the
    pre-open lstat and the open never follows a link or blocks on a pipe.
    The post-open fstat must agree with the pre-open lstat on device/inode
    identity, and a second fstat after the bounded read must still agree on
    device/inode/size/mtime, so a swap-in-place or truncation during the
    read is detected rather than silently hashed. Every raw OSError from
    fstat/read/close is translated into a bounded ContractError; a close
    failure never masks an already-asserted ContractError.
    """
    try:
        before = path.lstat()
    except OSError as error:
        raise ContractError(f"cannot stat source reconciliation {path}: {error}") from error
    if stat.S_ISLNK(before.st_mode):
        raise ContractError(f"source reconciliation {path} is a symlink, not a regular file")
    if not stat.S_ISREG(before.st_mode):
        kind = "directory" if stat.S_ISDIR(before.st_mode) else "non-regular file"
        raise ContractError(
            f"source reconciliation {path} is a {kind}, not a regular file"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise ContractError(f"cannot open source reconciliation {path}: {error}") from error
    try:
        try:
            after = os.fstat(fd)
        except OSError as error:
            raise ContractError(
                f"cannot stat source reconciliation {path}: {error}"
            ) from error
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ContractError(f"source reconciliation {path} changed while opening")
        if not stat.S_ISREG(after.st_mode):
            raise ContractError(f"source reconciliation {path} is not a regular file")
        size = after.st_size
        if size > AUTHORITY_FILE_BYTES_MAX:
            raise ContractError(
                f"source reconciliation {path} exceeds the "
                f"{AUTHORITY_FILE_BYTES_MAX}-byte bound"
            )
        chunks = []
        remaining = size
        while remaining > 0:
            try:
                chunk = os.read(fd, min(65536, remaining))
            except OSError as error:
                raise ContractError(
                    f"cannot read source reconciliation {path}: {error}"
                ) from error
            if not chunk:
                raise ContractError(
                    f"source reconciliation {path} was truncated while reading"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            final = os.fstat(fd)
        except OSError as error:
            raise ContractError(
                f"cannot stat source reconciliation {path}: {error}"
            ) from error
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise ContractError(f"source reconciliation {path} changed while reading")
        return b"".join(chunks)
    finally:
        pending = sys.exc_info()[0]
        try:
            os.close(fd)
        except OSError as error:
            if pending is None:
                raise ContractError(
                    f"cannot close source reconciliation {path}: {error}"
                ) from error


def load_source_reconciliation(path: Path) -> SourceReconciliation:
    raw_bytes = _read_no_follow_regular(path)

    def _pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        seen = set()
        for key, _value in pairs:
            if key in seen:
                raise ContractError(f"{path} has duplicate object key {key!r}")
            seen.add(key)
        return dict(pairs)

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError(f"cannot decode source reconciliation {path}: {error}") from error
    try:
        raw = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as error:
        raise ContractError(f"cannot parse source reconciliation {path}: {error}") from error
    obj = _object(raw, str(path))
    _strict_keys(
        obj,
        (
            "schema_version",
            "id",
            "predecessor_expectation",
            "successor_expectation",
            "target_source",
            "mappings",
            "design_gate_revision",
        ),
        str(path),
    )
    if obj["schema_version"] != SOURCE_RECONCILIATION_SCHEMA:
        raise ContractError(f"{path}.schema_version must equal {SOURCE_RECONCILIATION_SCHEMA!r}")
    reconciliation_id = _identifier(obj["id"], f"{path}.id")
    if reconciliation_id != SOURCE_RECONCILIATION_ID:
        raise ContractError(f"{path}.id must equal {SOURCE_RECONCILIATION_ID!r}")
    predecessor = _parse_predecessor_expectation(
        obj["predecessor_expectation"], f"{path}.predecessor_expectation"
    )
    successor = _parse_successor_expectation(
        obj["successor_expectation"], f"{path}.successor_expectation"
    )
    target = _parse_target_source(obj["target_source"], f"{path}.target_source")
    mappings = tuple(
        _parse_mapping(
            item,
            f"{path}.mappings[{index}]",
            predecessor.source_revision,
            target.revision,
        )
        for index, item in enumerate(_array(obj["mappings"], f"{path}.mappings"))
    )
    adr_numbers = [mapping.adr_number for mapping in mappings]
    if len(mappings) != len(EXACT_ADR_NUMBERS):
        raise ContractError(f"{path}.mappings must contain exactly seven mappings")
    if adr_numbers != list(EXACT_ADR_NUMBERS):
        raise ContractError(
            f"{path}.mappings ADR numbers must be the exact ordered set {list(EXACT_ADR_NUMBERS)}"
        )
    if len(set(adr_numbers)) != len(adr_numbers):
        raise ContractError(f"{path}.mappings contains duplicate ADR numbers")
    if hashlib.sha256(raw_bytes).hexdigest() != SOURCE_RECONCILIATION_V1_SHA256:
        raise ContractError(f"{path} does not match the canonical source reconciliation")
    try:
        source_path = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ContractError("cannot resolve source reconciliation") from exc
    return SourceReconciliation(
        schema_version=obj["schema_version"],
        id=reconciliation_id,
        predecessor_expectation=predecessor,
        successor_expectation=successor,
        target_source=target,
        mappings=mappings,
        design_gate_revision=_sha256(obj["design_gate_revision"], f"{path}.design_gate_revision"),
        source_path=source_path,
    )
