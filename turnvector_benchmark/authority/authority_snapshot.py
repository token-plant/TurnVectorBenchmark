"""Immutable AuthoritySnapshot input model for CoverageCompiler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ..canonical import (
    require_boolean,
    require_identifier,
    require_object,
    require_posix_path,
    require_sha256,
    require_strict_keys,
    require_string,
    require_u64,
)
from ..core import ContractError
from .bound_bytes import BoundBytesRef
from .contract_json import ParsedJson, parse_json_object

LOGICAL_NAME = "authority_snapshot"
SOURCE_RECONCILIATION_LOGICAL_PATH = "authority/source-reconciliation-v1.json"
SCHEMA_VERSION = "turnvector.benchmark.authority-snapshot.v1"
REPOSITORY_CONTROL_VERSION = "turnvector.benchmark.repository-control.v1"
STRICT_PARSER_VERSION = "canonical-strict-parser-v1"
CONTROL_NAME_KEY_RULE = "ascii-casefold-v1"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
NORMALIZED_ZERO_SHA256 = "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa"
NORMALIZED_FALSE_SHA256 = "2ed27c1421e6928dbe13dbfdb5c59e1045b30341fe7ebe05700006bc5ac572c0"
NORMALIZED_TRUE_SHA256 = "a17fcf0a2f50e2d495e4f90ce263410edc183add6c62699a2facbccf60410f74"

_DESCRIPTOR_FIELDS = (
    "schema_version", "strict_parser_version", "control_name_key_rule",
    "worktree_kind", "worktree_git_entry", "worktree_identity",
    "git_dir_identity", "common_dir_identity", "control_files", "ignore_files",
    "config_entries", "component_probe_witnesses", "recursive_scope_proofs",
    "absence_proofs", "proof_observations", "scan_counts", "command_results",
    "stability_entries", "observed_head", "clean", "stability_sha256",
    "qualified_preflight",
)
_IDENTITY_FIELDS = ("device", "inode", "uid", "gid", "mode", "size", "mtime_ns", "ctime_ns")
_ABSENCE_RULE_IDS = (
    "index-lock", "head-lock", "config-lock", "packed-refs-lock", "shallow-lock",
    "refs-locks", "alternates", "grafts", "replace-refs",
    "common-info-attributes", "git-info-attributes", "worktree-attributes",
    "gitlinks", "assume-unchanged", "skip-worktree", "config-includes",
    "unknown-config",
)
_SCOPE_KEYS = (
    "git-dir-control", "common-dir-control", "common-refs",
    "common-replace-refs", "worktree-control",
)


@dataclass(frozen=True)
class AuthoritySnapshot:
    snapshot_ref: BoundBytesRef
    source_reconciliation_ref: BoundBytesRef

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_ref, BoundBytesRef) or not isinstance(self.source_reconciliation_ref, BoundBytesRef):
            from .errors import CompilerPreconditionViolation

            raise CompilerPreconditionViolation("input_identity_mismatch")


@dataclass(frozen=True)
class ControlFileIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class StabilityIdentity:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class RootIdentity:
    absolute_path: str
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class SourceFileRecord:
    path: str
    byte_count: int
    sha256: str
    file_type: str
    no_follow_identity: ControlFileIdentity


@dataclass(frozen=True)
class SectionRecord:
    path: str
    start: int
    end: int
    sha256: str


@dataclass(frozen=True)
class HistoricalObjectRecord:
    revision: str
    path: str
    byte_count: int
    sha256: str
    git_object_type: str


@dataclass(frozen=True)
class WorktreeGitEntry:
    kind: str
    byte_count: int
    sha256: str
    no_follow_identity: ControlFileIdentity | None


@dataclass(frozen=True)
class ConfigEntry:
    namespace: str
    key: str
    value_sha256: str


@dataclass(frozen=True)
class RepositoryControlDescriptor:
    schema_version: str
    strict_parser_version: str
    control_name_key_rule: str
    worktree_kind: str
    worktree_git_entry: WorktreeGitEntry
    worktree_identity: RootIdentity
    git_dir_identity: RootIdentity
    common_dir_identity: RootIdentity
    control_files: tuple[Mapping[str, Any], ...]
    ignore_files: tuple[Mapping[str, Any], ...]
    config_entries: tuple[ConfigEntry, ...]
    component_probe_witnesses: Mapping[str, Mapping[str, Any]]
    recursive_scope_proofs: Mapping[str, Mapping[str, Any]]
    absence_proofs: tuple[Mapping[str, Any], ...]
    proof_observations: Mapping[str, tuple[Mapping[str, Any], ...]]
    scan_counts: Mapping[str, int]
    command_results: tuple[Mapping[str, Any], ...]
    stability_entries: tuple[Mapping[str, Any], ...]
    observed_head: str
    clean: bool
    stability_sha256: str
    qualified_preflight: bool


@dataclass(frozen=True)
class AuthoritySnapshotValue:
    schema_version: str
    repository: str
    revision: str
    observed_head: str
    revision_relation: str
    ancestry_verified: bool
    clean_required: bool
    clean: bool
    source_files: tuple[SourceFileRecord, ...]
    section_records: tuple[SectionRecord, ...]
    historical_objects: tuple[HistoricalObjectRecord, ...]
    repository_control: RepositoryControlDescriptor


def normalized_value_bytes(value: str | bool | int) -> bytes:
    """Return the exact V28 semantic-scalar digest preimage."""
    if isinstance(value, bool):
        pass
    elif isinstance(value, int):
        require_u64(value, "normalized config value")
    elif not isinstance(value, str):
        raise ContractError("normalized config value must be a string, boolean, or u64")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def normalized_value_sha256(value: str | bool | int) -> str:
    return hashlib.sha256(normalized_value_bytes(value)).hexdigest()


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _record(value: Any, fields: tuple[str, ...], where: str) -> Mapping[str, Any]:
    mapping = require_object(value, where)
    require_strict_keys(mapping, fields, where)
    return mapping


def _nullable(value: Any, parser: Any, where: str) -> Any:
    return None if value is None else parser(value, where)


def _identity(value: Any, where: str, cls: Any = ControlFileIdentity) -> Any:
    mapping = _record(value, _IDENTITY_FIELDS, where)
    return cls(*(require_u64(mapping[name], f"{where}.{name}") for name in _IDENTITY_FIELDS))


def _absolute_path(value: Any, where: str) -> str:
    text = require_string(value, where)
    if not text.startswith("/") or "\x00" in text or (text != "/" and text.endswith("/")):
        raise ContractError(f"{where} must be an absolute POSIX path")
    if text != "/" and any(component in ("", ".", "..") for component in text[1:].split("/")):
        raise ContractError(f"{where} must be an absolute POSIX path")
    return text


def _root_identity(value: Any, where: str) -> RootIdentity:
    fields = ("absolute_path",) + _IDENTITY_FIELDS
    mapping = _record(value, fields, where)
    return RootIdentity(_absolute_path(mapping["absolute_path"], f"{where}.absolute_path"), *(require_u64(mapping[name], f"{where}.{name}") for name in _IDENTITY_FIELDS))


def _freeze_json(value: Any, where: str) -> Any:
    """Recursively freeze an already structurally admitted JSON value."""
    if isinstance(value, dict):
        return MappingProxyType({require_string(key, f"{where} key"): _freeze_json(child, f"{where}.{key}") for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(child, f"{where}[{i}]") for i, child in enumerate(value))
    if value is None or isinstance(value, (str, bool)) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise ContractError(f"{where} contains a noncanonical JSON value")


def _frozen_record(value: Any, where: str) -> Mapping[str, Any]:
    return _freeze_json(require_object(value, where), where)


def _source_file(value: Any, where: str) -> SourceFileRecord:
    m = _record(value, ("path", "byte_count", "sha256", "file_type", "no_follow_identity"), where)
    return SourceFileRecord(require_posix_path(m["path"], f"{where}.path"), require_u64(m["byte_count"], f"{where}.byte_count"), require_sha256(m["sha256"], f"{where}.sha256"), require_string(m["file_type"], f"{where}.file_type"), _identity(m["no_follow_identity"], f"{where}.no_follow_identity"))


def _section(value: Any, where: str) -> SectionRecord:
    m = _record(value, ("path", "start", "end", "sha256"), where)
    return SectionRecord(require_posix_path(m["path"], f"{where}.path"), require_u64(m["start"], f"{where}.start"), require_u64(m["end"], f"{where}.end"), require_sha256(m["sha256"], f"{where}.sha256"))


def _historical(value: Any, where: str) -> HistoricalObjectRecord:
    m = _record(value, ("revision", "path", "byte_count", "sha256", "git_object_type"), where)
    revision = require_string(m["revision"], f"{where}.revision")
    return HistoricalObjectRecord(revision, require_posix_path(m["path"], f"{where}.path"), require_u64(m["byte_count"], f"{where}.byte_count"), require_sha256(m["sha256"], f"{where}.sha256"), require_string(m["git_object_type"], f"{where}.git_object_type"))


def _worktree_git_entry(value: Any, where: str) -> WorktreeGitEntry:
    m = _record(value, ("kind", "byte_count", "sha256", "no_follow_identity"), where)
    return WorktreeGitEntry(require_string(m["kind"], f"{where}.kind"), require_u64(m["byte_count"], f"{where}.byte_count"), require_sha256(m["sha256"], f"{where}.sha256"), _nullable(m["no_follow_identity"], _identity, f"{where}.no_follow_identity"))


def _config_entry(value: Any, where: str) -> ConfigEntry:
    m = _record(value, ("namespace", "key", "value_sha256"), where)
    return ConfigEntry(require_string(m["namespace"], f"{where}.namespace"), require_string(m["key"], f"{where}.key"), require_sha256(m["value_sha256"], f"{where}.value_sha256"))


def repository_control_descriptor(value: Any) -> RepositoryControlDescriptor:
    where = "authority_snapshot.repository_control"
    m = _record(value, _DESCRIPTOR_FIELDS, where)
    for field, expected in (("schema_version", REPOSITORY_CONTROL_VERSION), ("strict_parser_version", STRICT_PARSER_VERSION), ("control_name_key_rule", CONTROL_NAME_KEY_RULE)):
        if m[field] != expected:
            raise ContractError(f"{where}.{field} must equal {expected!r}")
    configs = tuple(_config_entry(v, f"{where}.config_entries[{i}]") for i, v in enumerate(_array(m["config_entries"], f"{where}.config_entries")))
    witnesses = require_object(m["component_probe_witnesses"], f"{where}.component_probe_witnesses")
    scopes = _record(m["recursive_scope_proofs"], _SCOPE_KEYS, f"{where}.recursive_scope_proofs")
    observations = _record(m["proof_observations"], _ABSENCE_RULE_IDS, f"{where}.proof_observations")
    scan = _record(m["scan_counts"], ("entry_count", "path_bytes", "name_bytes_max_observed", "directory_entries_max_observed", "sort_index_bytes_max_observed", "ignore_file_count", "ignore_bytes"), f"{where}.scan_counts")
    return RepositoryControlDescriptor(
        REPOSITORY_CONTROL_VERSION, STRICT_PARSER_VERSION, CONTROL_NAME_KEY_RULE,
        require_string(m["worktree_kind"], f"{where}.worktree_kind"), _worktree_git_entry(m["worktree_git_entry"], f"{where}.worktree_git_entry"),
        _root_identity(m["worktree_identity"], f"{where}.worktree_identity"), _root_identity(m["git_dir_identity"], f"{where}.git_dir_identity"), _root_identity(m["common_dir_identity"], f"{where}.common_dir_identity"),
        tuple(_frozen_record(v, f"{where}.control_files[{i}]") for i, v in enumerate(_array(m["control_files"], f"{where}.control_files"))),
        tuple(_frozen_record(v, f"{where}.ignore_files[{i}]") for i, v in enumerate(_array(m["ignore_files"], f"{where}.ignore_files"))), configs,
        MappingProxyType({require_sha256(k, f"{where}.component_probe_witnesses key"): _frozen_record(v, f"{where}.component_probe_witnesses.{k}") for k, v in witnesses.items()}),
        MappingProxyType({k: _frozen_record(scopes[k], f"{where}.recursive_scope_proofs.{k}") for k in _SCOPE_KEYS}),
        tuple(_frozen_record(v, f"{where}.absence_proofs[{i}]") for i, v in enumerate(_array(m["absence_proofs"], f"{where}.absence_proofs"))),
        MappingProxyType({k: tuple(_frozen_record(v, f"{where}.proof_observations.{k}[{i}]") for i, v in enumerate(_array(observations[k], f"{where}.proof_observations.{k}"))) for k in _ABSENCE_RULE_IDS}),
        MappingProxyType({k: require_u64(scan[k], f"{where}.scan_counts.{k}") for k in scan}),
        tuple(_frozen_record(v, f"{where}.command_results[{i}]") for i, v in enumerate(_array(m["command_results"], f"{where}.command_results"))),
        tuple(_frozen_record(v, f"{where}.stability_entries[{i}]") for i, v in enumerate(_array(m["stability_entries"], f"{where}.stability_entries"))),
        require_string(m["observed_head"], f"{where}.observed_head"), require_boolean(m["clean"], f"{where}.clean"), require_sha256(m["stability_sha256"], f"{where}.stability_sha256"), require_boolean(m["qualified_preflight"], f"{where}.qualified_preflight"),
    )


def authority_snapshot_value(value: ParsedJson | Any) -> AuthoritySnapshotValue:
    if isinstance(value, ParsedJson):
        value = value.value
    fields = ("schema_version", "repository", "revision", "observed_head", "revision_relation", "ancestry_verified", "clean_required", "clean", "source_files", "section_records", "historical_objects", "repository_control")
    m = _record(value, fields, LOGICAL_NAME)
    if m["schema_version"] != SCHEMA_VERSION:
        raise ContractError(f"{LOGICAL_NAME}.schema_version must equal {SCHEMA_VERSION!r}")
    def records(name: str, parser: Any) -> tuple[Any, ...]:
        return tuple(parser(v, f"{LOGICAL_NAME}.{name}[{i}]") for i, v in enumerate(_array(m[name], f"{LOGICAL_NAME}.{name}")))
    return AuthoritySnapshotValue(SCHEMA_VERSION, require_string(m["repository"], f"{LOGICAL_NAME}.repository"), require_string(m["revision"], f"{LOGICAL_NAME}.revision"), require_string(m["observed_head"], f"{LOGICAL_NAME}.observed_head"), require_identifier(m["revision_relation"], f"{LOGICAL_NAME}.revision_relation"), require_boolean(m["ancestry_verified"], f"{LOGICAL_NAME}.ancestry_verified"), require_boolean(m["clean_required"], f"{LOGICAL_NAME}.clean_required"), require_boolean(m["clean"], f"{LOGICAL_NAME}.clean"), records("source_files", _source_file), records("section_records", _section), records("historical_objects", _historical), repository_control_descriptor(m["repository_control"]))


def parse_authority_snapshot(envelope: AuthoritySnapshot) -> ParsedJson:
    """Stage-1 parse while retaining duplicate keys and declaration order."""
    return parse_json_object(envelope.snapshot_ref.buffer, LOGICAL_NAME)
