"""Immutable canonical ``CoveragePlan`` output value and builder.

The logical field order below documents the closed schema.  Serialization is
always compact JSON with every object key sorted recursively, ASCII escaping,
and exactly one trailing LF; array order is never changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Iterator, Tuple

from ..compile_limits import CompileLimits
from ..core import ContractError

SCHEMA_VERSION = "turnvector.benchmark.coverage-plan.v1"
PROFILE_ID = "turnvector-implementation-v2"
OUTCOME = "coverage_plan"
REPOSITORY_CONTROL_VERSION = "turnvector.benchmark.repository-control.v1"
T_MAX = 8
U64_MAX = (1 << 64) - 1

AUTHORITY_PATHS = {
    "source_reconciliation_path": "authority/source-reconciliation-v1.json",
    "expectation_path": "expectations/turnvector-implementation-v2.json",
    "catalog_path": "authority/obligation-catalog-v1.jsonl",
    "traceability_path": "authority/traceability-v1.json",
}
LIMITATIONS = (
    "plan proves closure relative to the accepted obligation catalog and exact source snapshot only",
    "no production behavior, adapter availability, or semantic completeness of source prose is claimed",
    "plan binds the through-START chronology prefix only; the final history digest/count is first persisted by RunEnvironment",
    "structural fixture readiness does not imply claim readiness",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

_INPUT_FIELDS = (
    "source_reconciliation_path", "source_reconciliation_sha256",
    "expectation_path", "expectation_sha256", "catalog_path", "catalog_sha256",
    "traceability_path", "traceability_sha256", "authority_snapshot_sha256",
    "compile_limits_sha256", "traceability_bindings",
)
_BIND_FIELDS = (
    "source_reconciliation_sha256", "expectation_sha256", "catalog_sha256",
    "compile_custody_policy_sha256", "custody_domain_sha256", "lane_suite_sha256",
    "case_schema_sha256", "judge_contract_sha256", "evidence_bundle_contract_sha256",
    "raw_evidence_source_sha256", "lane_context_contract_sha256",
    "post_gate_output_contract_sha256", "gate_sha256", "judge_negative_sha256",
    "aggregate_negative_sha256", "plumbing_negative_sha256",
)
_SOURCE_FIELDS = (
    "repository", "revision", "observed_head", "revision_relation", "clean_required",
    "clean", "repository_control_sha256", "repository_control_version", "source_files",
    "source_files_sha256", "section_records", "section_records_sha256",
    "historical_objects", "historical_objects_sha256",
)
_ENTITY_FIELDS = (
    "lane_sha256", "behavior_case_sha256", "case_plan_sha256", "obligation_sha256",
    "judge_sha256", "evidence_bundle_sha256", "evidence_source_sha256",
    "lane_context_contract_sha256", "post_gate_output_contract_sha256", "gate_sha256",
    "judge_negative_template_sha256", "aggregate_negative_template_sha256",
    "plumbing_negative_template_sha256", "enforcement_path_sha256",
)
_RELATION_FIELDS = (
    "behavior_obligation_sha256", "obligation_judge_sha256", "lane_bundle_sha256",
    "obligation_gate_sha256", "bundle_evidence_sha256", "lane_context_sha256",
    "lane_output_sha256", "path_case_sha256", "path_gate_sha256",
    "path_evidence_sha256", "path_judge_negative_sha256",
    "gate_aggregate_negative_sha256", "gate_plumbing_negative_sha256",
)
_COUNT_FIELDS = (
    "lane_count", "behavior_case_count", "case_plan_count", "obligation_count",
    "judge_count", "evidence_bundle_count", "evidence_source_count",
    "lane_context_contract_count", "post_gate_output_contract_count", "gate_count",
    "judge_negative_template_count", "aggregate_negative_template_count",
    "plumbing_negative_template_count", "obligation_gate_pair_count",
    "enforcement_path_count", "bundle_evidence_record_count", "path_case_record_count",
    "path_gate_record_count", "path_evidence_record_count",
    "path_judge_negative_record_count", "gate_aggregate_negative_record_count",
    "gate_plumbing_negative_record_count", "lane_context_record_count",
    "lane_output_record_count", "artifact_membership_total",
    "relation_path_record_count", "relation_support_record_count",
    "relation_total_record_count", "endpoint_path_reference_count",
    "endpoint_support_reference_count", "endpoint_total_reference_count",
    "entity_record_count", "relation_only_record_count", "serialized_input_byte_count",
    "authority_file_count", "authority_byte_count", "section_count", "section_byte_count",
    "logical_arena_byte_count", "key_count_exec", "key_count_run",
)
_EXPECTED_KEY_FIELDS = (
    "names", "count", "sha256", "compile_key_count", "execution_key_count",
    "run_global_key_count",
)
_LIMIT_FIELDS = tuple(CompileLimits.__dataclass_fields__)


class FrozenDict(Mapping[str, Any]):
    """Small immutable, hashable mapping used for nested output objects."""

    __slots__ = ("_items", "_dict")

    def __init__(self, items: Mapping[str, Any]) -> None:
        self._items = tuple(sorted(items.items(), key=lambda item: item[0].encode("utf-8")))
        self._dict = dict(self._items)

    def __getitem__(self, key: str) -> Any:
        return self._dict[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)


def _strict_mapping(value: Any, fields: Tuple[str, ...], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{where} must be an object")
    missing = sorted(set(fields) - set(value))
    unknown = sorted(set(value) - set(fields))
    if missing or unknown:
        raise ContractError(f"{where} has an inexact field set")
    return value


def _u64(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= U64_MAX:
        raise ContractError(f"{where} must be a nonnegative u64")
    return value


def _sha256(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ContractError(f"{where} must be a lowercase SHA-256 digest")
    return value


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ContractError(f"{where} must be an identifier")
    return value


def _freeze(value: Any, where: str) -> Any:
    if isinstance(value, Mapping):
        frozen = {}
        for key, child in value.items():
            if not isinstance(key, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in key):
                raise ContractError(f"{where} contains an invalid object key")
            frozen[key] = _freeze(child, f"{where}.{key}")
        return FrozenDict(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child, f"{where}[{index}]") for index, child in enumerate(value))
    if isinstance(value, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ContractError(f"{where} contains an unpaired surrogate")
        return value
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _u64(value, where)
    raise ContractError(f"{where} is not a canonical JSON value")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def canonical_object_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize one closed output object as canonical compact JSON plus LF."""
    try:
        text = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return text.encode("ascii") + b"\n"
    except (RecursionError, TypeError, ValueError, UnicodeError) as error:
        raise ContractError("cannot encode canonical output object") from error


def _validate_bindings(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _BIND_FIELDS, "inputs.traceability_bindings")
    for name in _BIND_FIELDS[:5]:
        _sha256(obj[name], f"inputs.traceability_bindings.{name}")
    expected_counts = (12, 12, 46, 12, 41, 2, 2, 58, 46, 58, 58)
    for name, count in zip(_BIND_FIELDS[5:], expected_counts):
        child = obj[name]
        if not isinstance(child, Mapping) or len(child) != count:
            raise ContractError(f"inputs.traceability_bindings.{name} must have {count} entries")
        for key, digest in child.items():
            _identifier(key, f"inputs.traceability_bindings.{name} key")
            _sha256(digest, f"inputs.traceability_bindings.{name}.{key}")
    if set(obj["lane_context_contract_sha256"]) != {"manifest", "environment"}:
        raise ContractError("lane_context_contract_sha256 has the wrong keys")
    if set(obj["post_gate_output_contract_sha256"]) != {"report", "checksums"}:
        raise ContractError("post_gate_output_contract_sha256 has the wrong keys")
    return _freeze(obj, "inputs.traceability_bindings")


def _validate_inputs(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _INPUT_FIELDS, "inputs")
    for name, expected in AUTHORITY_PATHS.items():
        if obj[name] != expected:
            raise ContractError(f"inputs.{name} must equal {expected!r}")
    for name in _INPUT_FIELDS:
        if name.endswith("_sha256"):
            _sha256(obj[name], f"inputs.{name}")
    result = dict(obj)
    result["traceability_bindings"] = _validate_bindings(obj["traceability_bindings"])
    return _freeze(result, "inputs")


def _validate_source(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _SOURCE_FIELDS, "source")
    if not isinstance(obj["repository"], str) or not obj["repository"]:
        raise ContractError("source.repository must be a nonempty string")
    for name in ("revision", "observed_head"):
        if not isinstance(obj[name], str) or not _REVISION_RE.fullmatch(obj[name]):
            raise ContractError(f"source.{name} must be a lowercase 40-hex revision")
    if obj["revision_relation"] not in ("exact", "descendant"):
        raise ContractError("source.revision_relation is invalid")
    if not isinstance(obj["clean_required"], bool) or not isinstance(obj["clean"], bool):
        raise ContractError("source clean fields must be booleans")
    if obj["repository_control_version"] != REPOSITORY_CONTROL_VERSION:
        raise ContractError("source.repository_control_version is invalid")
    for name in ("repository_control_sha256", "source_files_sha256",
                 "section_records_sha256", "historical_objects_sha256"):
        _sha256(obj[name], f"source.{name}")
    record_specs = (
        ("source_files", ("path", "byte_count", "sha256", "file_type"), "regular"),
        ("section_records", ("path", "start", "end", "sha256"), None),
        ("historical_objects", ("revision", "path", "byte_count", "sha256", "git_object_type"), "blob"),
    )
    for array_name, fields, type_const in record_specs:
        records = obj[array_name]
        if not isinstance(records, (list, tuple)):
            raise ContractError(f"source.{array_name} must be an array")
        for index, record in enumerate(records):
            item = _strict_mapping(record, fields, f"source.{array_name}[{index}]")
            if not isinstance(item["path"], str) or not item["path"]:
                raise ContractError(f"source.{array_name}[{index}].path is invalid")
            _sha256(item["sha256"], f"source.{array_name}[{index}].sha256")
            for number in ("byte_count", "start", "end"):
                if number in item:
                    _u64(item[number], f"source.{array_name}[{index}].{number}")
            if "revision" in item and not _REVISION_RE.fullmatch(item["revision"]):
                raise ContractError(f"source.{array_name}[{index}].revision is invalid")
            if type_const is not None and item[fields[-1]] != type_const:
                raise ContractError(f"source.{array_name}[{index}] has an invalid type")
    return _freeze(obj, "source")


def _digest_object(value: Any, fields: Tuple[str, ...], where: str) -> FrozenDict:
    obj = _strict_mapping(value, fields, where)
    for name in fields:
        _sha256(obj[name], f"{where}.{name}")
    return _freeze(obj, where)


def _validate_counts(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _COUNT_FIELDS, "counts")
    for name in _COUNT_FIELDS:
        _u64(obj[name], f"counts.{name}")
    return _freeze(obj, "counts")


def _validate_limits(value: Any) -> FrozenDict:
    if isinstance(value, CompileLimits):
        obj = {name: getattr(value, name) for name in _LIMIT_FIELDS}
    else:
        obj = _strict_mapping(value, _LIMIT_FIELDS, "limits")
    frozen = CompileLimits.frozen()
    for name in _LIMIT_FIELDS:
        if obj[name] != getattr(frozen, name):
            raise ContractError(f"limits.{name} must equal the frozen value")
    return _freeze(obj, "limits")


def _validate_expected_keys(value: Any, counts: Mapping[str, Any], t_max: int) -> FrozenDict:
    obj = _strict_mapping(value, _EXPECTED_KEY_FIELDS, "expected_keys")
    names = obj["names"]
    if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) or not name for name in names):
        raise ContractError("expected_keys.names must be an array of nonempty strings")
    try:
        encoded = [name.encode("ascii") for name in names]
    except UnicodeEncodeError as error:
        raise ContractError("expected_keys.names must be ASCII") from error
    if encoded != sorted(set(encoded)):
        raise ContractError("expected_keys.names must be duplicate-free and byte-lexically sorted")
    for name in _EXPECTED_KEY_FIELDS[1:2] + _EXPECTED_KEY_FIELDS[3:]:
        _u64(obj[name], f"expected_keys.{name}")
    _sha256(obj["sha256"], "expected_keys.sha256")
    digest = hashlib.sha256(b"".join(name + b"\n" for name in encoded)).hexdigest()
    if obj["count"] != len(names) or obj["sha256"] != digest:
        raise ContractError("expected_keys count or digest disagrees with names")
    if obj["compile_key_count"] != 2 * t_max + 1:
        raise ContractError("expected_keys.compile_key_count is invalid")
    if obj["execution_key_count"] != counts["key_count_exec"]:
        raise ContractError("expected_keys.execution_key_count is invalid")
    if obj["run_global_key_count"] != 5:
        raise ContractError("expected_keys.run_global_key_count is invalid")
    if obj["count"] != sum(obj[name] for name in
                            ("compile_key_count", "execution_key_count", "run_global_key_count")):
        raise ContractError("expected_keys classifications do not cover names")
    return _freeze(obj, "expected_keys")


@dataclass(frozen=True)
class CoveragePlan:
    schema_version: str
    profile_id: str
    outcome: str
    custody_domain_id: str
    custody_domain_sha256: str
    custody_lineage_id: str
    attempt: int
    t_max: int
    start_event_sha256: str
    chronology_prefix_sha256: str
    chronology_prefix_byte_count: int
    compiler_build_sha256: str
    execution_closure_sha256: str
    compile_custody_policy_sha256: str
    input_set_sha256: str
    inputs: Mapping[str, Any]
    source: Mapping[str, Any]
    entity_sets: Mapping[str, Any]
    relation_sets: Mapping[str, Any]
    counts: Mapping[str, Any]
    limits: Mapping[str, Any]
    expected_keys: Mapping[str, Any]
    limitations: Tuple[str, ...]
    _canonical: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.profile_id != PROFILE_ID or self.outcome != OUTCOME:
            raise ContractError("CoveragePlan identity constants are invalid")
        _identifier(self.custody_domain_id, "custody_domain_id")
        _identifier(self.custody_lineage_id, "custody_lineage_id")
        for name in ("custody_domain_sha256", "start_event_sha256", "chronology_prefix_sha256",
                     "compiler_build_sha256", "execution_closure_sha256",
                     "compile_custody_policy_sha256", "input_set_sha256"):
            _sha256(getattr(self, name), name)
        for name in ("attempt", "t_max", "chronology_prefix_byte_count"):
            _u64(getattr(self, name), name)
        if not 1 <= self.attempt <= self.t_max or self.t_max != T_MAX:
            raise ContractError("CoveragePlan attempt/t_max is invalid")
        inputs = _validate_inputs(self.inputs)
        if inputs["source_reconciliation_sha256"] != inputs["traceability_bindings"]["source_reconciliation_sha256"]:
            raise ContractError("source reconciliation binding disagrees")
        if inputs["expectation_sha256"] != inputs["traceability_bindings"]["expectation_sha256"]:
            raise ContractError("expectation binding disagrees")
        if inputs["catalog_sha256"] != inputs["traceability_bindings"]["catalog_sha256"]:
            raise ContractError("catalog binding disagrees")
        if self.input_set_sha256 == "":  # grammar check above; retained as an explicit identity field
            raise ContractError("input_set_sha256 is invalid")
        source = _validate_source(self.source)
        entity_sets = _digest_object(self.entity_sets, _ENTITY_FIELDS, "entity_sets")
        relation_sets = _digest_object(self.relation_sets, _RELATION_FIELDS, "relation_sets")
        counts = _validate_counts(self.counts)
        limits = _validate_limits(self.limits)
        expected_keys = _validate_expected_keys(self.expected_keys, counts, self.t_max)
        limitations = tuple(self.limitations)
        if limitations != LIMITATIONS:
            raise ContractError("CoveragePlan limitations must equal the four frozen entries")
        for name, value in (("inputs", inputs), ("source", source), ("entity_sets", entity_sets),
                            ("relation_sets", relation_sets), ("counts", counts), ("limits", limits),
                            ("expected_keys", expected_keys), ("limitations", limitations)):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_canonical", canonical_object_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {name: _plain(getattr(self, name)) for name in (
            "schema_version", "profile_id", "outcome", "custody_domain_id",
            "custody_domain_sha256", "custody_lineage_id", "attempt", "t_max",
            "start_event_sha256", "chronology_prefix_sha256", "chronology_prefix_byte_count",
            "compiler_build_sha256", "execution_closure_sha256",
            "compile_custody_policy_sha256", "input_set_sha256", "inputs", "source",
            "entity_sets", "relation_sets", "counts", "limits", "expected_keys", "limitations",
        )}

    @property
    def canonical_bytes(self) -> bytes:
        return self._canonical

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._canonical).hexdigest()

    @property
    def byte_count(self) -> int:
        return len(self._canonical)

    @classmethod
    def build(cls, **fields: Any) -> "CoveragePlan":
        fields.setdefault("schema_version", SCHEMA_VERSION)
        fields.setdefault("profile_id", PROFILE_ID)
        fields.setdefault("outcome", OUTCOME)
        fields.setdefault("limitations", LIMITATIONS)
        return cls(**fields)


def build_coverage_plan(**fields: Any) -> CoveragePlan:
    """Build and fully validate one immutable canonical CoveragePlan."""
    return CoveragePlan.build(**fields)
