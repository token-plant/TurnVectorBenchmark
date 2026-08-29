"""Immutable TraceabilityLedger input model for CoverageCompiler."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ..canonical import (
    require_identifier,
    require_object,
    require_sha256,
    require_strict_keys,
    require_u64,
)
from ..core import ContractError
from .bound_bytes import BoundBytesRef
from .contract_json import ParsedJson, parse_json_object

LOGICAL_PATH = "authority/traceability-v1.json"
LOGICAL_NAME = "traceability_ledger"
SCHEMA_VERSION = "turnvector.benchmark.traceability.v1"
PROFILE_ID = "turnvector-implementation-v2"

LANE_OBLIGATION_COUNTS = (4, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4)
LANE_CASE_COUNTS = (20, 3, 40, 15, 18, 36, 80, 36, 36, 22, 24, 95)
LANE_GATE_COUNTS = (3, 3, 4, 5, 4, 4, 7, 7, 7, 4, 5, 5)
LANE_RAW_MEMBERSHIP_COUNTS = (3, 1, 4, 4, 5, 4, 5, 5, 6, 7, 4, 4)
LANE_CONTEXT_COUNTS = (1, 2, 2, 1, 2, 2, 2, 2, 2, 1, 2, 2)
LANE_OUTPUT_COUNTS = (1, 2, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2)

_CARDINALITY_FIELDS = (
    "lane_count", "case_plan_count", "gate_count", "obligation_count",
    "judge_count", "evidence_bundle_count", "evidence_source_count",
    "lane_context_membership_count", "post_gate_output_membership_count",
    "artifact_membership_total", "judge_negative_template_count",
    "aggregate_negative_template_count", "plumbing_negative_template_count",
    "lane_obligation_counts", "lane_case_counts", "lane_gate_counts",
    "lane_raw_membership_counts", "lane_context_counts", "lane_output_counts",
    "obligation_gate_pair_count", "enforcement_path_count",
    "evidence_membership_record_count", "path_evidence_record_count",
    "endpoint_reference_count", "relation_record_count", "entity_record_count",
    "key_count_exec",
)
_BIND_FIELDS = (
    "source_reconciliation_sha256", "expectation_sha256", "catalog_sha256",
    "compile_custody_policy_sha256", "custody_domain_sha256", "lane_suite_sha256",
    "case_schema_sha256", "judge_contract_sha256",
    "evidence_bundle_contract_sha256", "raw_evidence_source_sha256",
    "lane_context_contract_sha256", "post_gate_output_contract_sha256",
    "gate_sha256", "judge_negative_sha256", "aggregate_negative_sha256",
    "plumbing_negative_sha256",
)
_ENTITY_FIELDS = (
    "judge_ids", "obligation_to_judge", "evidence_bundles",
    "evidence_source_ids", "evidence_bundle_memberships", "lane_contexts",
    "post_gate_outputs", "gates", "obligation_gate_pairs",
    "judge_negative_tests", "aggregate_negative_tests", "plumbing_negative_tests",
)


@dataclass(frozen=True)
class TraceabilityLedger:
    ledger_ref: BoundBytesRef

    def __post_init__(self) -> None:
        if not isinstance(self.ledger_ref, BoundBytesRef):
            from .errors import CompilerPreconditionViolation

            raise CompilerPreconditionViolation("input_identity_mismatch")


@dataclass(frozen=True)
class Predecessor:
    compile_custody_lineage_id: str
    chronology_sha256: str


@dataclass(frozen=True)
class Cardinalities:
    lane_count: int
    case_plan_count: int
    gate_count: int
    obligation_count: int
    judge_count: int
    evidence_bundle_count: int
    evidence_source_count: int
    lane_context_membership_count: int
    post_gate_output_membership_count: int
    artifact_membership_total: int
    judge_negative_template_count: int
    aggregate_negative_template_count: int
    plumbing_negative_template_count: int
    lane_obligation_counts: tuple[int, ...]
    lane_case_counts: tuple[int, ...]
    lane_gate_counts: tuple[int, ...]
    lane_raw_membership_counts: tuple[int, ...]
    lane_context_counts: tuple[int, ...]
    lane_output_counts: tuple[int, ...]
    obligation_gate_pair_count: int
    enforcement_path_count: int
    evidence_membership_record_count: int
    path_evidence_record_count: int
    endpoint_reference_count: int
    relation_record_count: int
    entity_record_count: int
    key_count_exec: int


@dataclass(frozen=True)
class DigestBindings:
    source_reconciliation_sha256: str
    expectation_sha256: str
    catalog_sha256: str
    compile_custody_policy_sha256: str
    custody_domain_sha256: str
    lane_suite_sha256: Mapping[str, str]
    case_schema_sha256: Mapping[str, str]
    judge_contract_sha256: Mapping[str, str]
    evidence_bundle_contract_sha256: Mapping[str, str]
    raw_evidence_source_sha256: Mapping[str, str]
    lane_context_contract_sha256: Mapping[str, str]
    post_gate_output_contract_sha256: Mapping[str, str]
    gate_sha256: Mapping[str, str]
    judge_negative_sha256: Mapping[str, str]
    aggregate_negative_sha256: Mapping[str, str]
    plumbing_negative_sha256: Mapping[str, str]


@dataclass(frozen=True)
class JudgeBinding:
    obligation_id: str
    judge_id: str


@dataclass(frozen=True)
class EvidenceBundle:
    id: str
    lane_id: str


@dataclass(frozen=True)
class EvidenceMembership:
    bundle_id: str
    evidence_source_id: str


@dataclass(frozen=True)
class LaneContext:
    lane_id: str
    artifact_type: str


@dataclass(frozen=True)
class LaneOutput:
    lane_id: str
    artifact_type: str


@dataclass(frozen=True)
class GateRecord:
    lane_id: str
    gate_id: str


@dataclass(frozen=True)
class ObligationGatePair:
    lane_id: str
    obligation_id: str
    gate_id: str


@dataclass(frozen=True)
class JudgeNegativeBinding:
    owner_id: str
    id: str


@dataclass(frozen=True)
class AggregateNegativeBinding:
    owner_id: str
    id: str


@dataclass(frozen=True)
class PlumbingNegativeBinding:
    owner_id: str
    id: str


@dataclass(frozen=True)
class LedgerEntities:
    judge_ids: tuple[str, ...]
    obligation_to_judge: tuple[JudgeBinding, ...]
    evidence_bundles: tuple[EvidenceBundle, ...]
    evidence_source_ids: tuple[str, ...]
    evidence_bundle_memberships: tuple[EvidenceMembership, ...]
    lane_contexts: tuple[LaneContext, ...]
    post_gate_outputs: tuple[LaneOutput, ...]
    gates: tuple[GateRecord, ...]
    obligation_gate_pairs: tuple[ObligationGatePair, ...]
    judge_negative_tests: tuple[JudgeNegativeBinding, ...]
    aggregate_negative_tests: tuple[AggregateNegativeBinding, ...]
    plumbing_negative_tests: tuple[PlumbingNegativeBinding, ...]


@dataclass(frozen=True)
class TraceabilityLedgerValue:
    schema_version: str
    id: str
    profile_id: str
    design_gate_revision: str
    predecessor: Predecessor | None
    cardinalities: Cardinalities
    binds: DigestBindings
    entities: LedgerEntities


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _identifier_array(value: Any, where: str) -> tuple[str, ...]:
    return tuple(require_identifier(item, f"{where}[{i}]") for i, item in enumerate(_array(value, where)))


def _u64_array(value: Any, where: str) -> tuple[int, ...]:
    return tuple(require_u64(item, f"{where}[{i}]") for i, item in enumerate(_array(value, where)))


def _digest_map(value: Any, where: str) -> Mapping[str, str]:
    mapping = require_object(value, where)
    return MappingProxyType({
        require_identifier(key, f"{where} key"): require_sha256(digest, f"{where}.{key}")
        for key, digest in mapping.items()
    })


def _record(value: Any, fields: tuple[str, ...], where: str) -> Mapping[str, Any]:
    mapping = require_object(value, where)
    require_strict_keys(mapping, fields, where)
    return mapping


def _two_ids(value: Any, first: str, second: str, where: str) -> tuple[str, str]:
    mapping = _record(value, (first, second), where)
    return require_identifier(mapping[first], f"{where}.{first}"), require_identifier(mapping[second], f"{where}.{second}")


def traceability_ledger_value(value: ParsedJson | Any) -> TraceabilityLedgerValue:
    """Build the immutable value after duplicate/order facts are dispatched."""
    if isinstance(value, ParsedJson):
        value = value.value
    root = _record(value, ("schema_version", "id", "profile_id", "design_gate_revision", "predecessor", "cardinalities", "binds", "entities"), "traceability_ledger")
    if root["schema_version"] != SCHEMA_VERSION or root["profile_id"] != PROFILE_ID:
        raise ContractError("traceability_ledger has an unsupported const value")
    predecessor_value = root["predecessor"]
    predecessor = None
    if predecessor_value is not None:
        p = _record(predecessor_value, ("compile_custody_lineage_id", "chronology_sha256"), "traceability_ledger.predecessor")
        predecessor = Predecessor(require_identifier(p["compile_custody_lineage_id"], "traceability_ledger.predecessor.compile_custody_lineage_id"), require_sha256(p["chronology_sha256"], "traceability_ledger.predecessor.chronology_sha256"))

    c = _record(root["cardinalities"], _CARDINALITY_FIELDS, "traceability_ledger.cardinalities")
    array_names = {"lane_obligation_counts", "lane_case_counts", "lane_gate_counts", "lane_raw_membership_counts", "lane_context_counts", "lane_output_counts"}
    cardinality_values = {name: (_u64_array(c[name], f"traceability_ledger.cardinalities.{name}") if name in array_names else require_u64(c[name], f"traceability_ledger.cardinalities.{name}")) for name in _CARDINALITY_FIELDS}

    b = _record(root["binds"], _BIND_FIELDS, "traceability_ledger.binds")
    scalar_binds = _BIND_FIELDS[:5]
    bind_values: dict[str, Any] = {name: require_sha256(b[name], f"traceability_ledger.binds.{name}") for name in scalar_binds}
    for name in _BIND_FIELDS[5:]:
        bind_values[name] = _digest_map(b[name], f"traceability_ledger.binds.{name}")

    e = _record(root["entities"], _ENTITY_FIELDS, "traceability_ledger.entities")
    def records(name: str, factory: Any) -> tuple[Any, ...]:
        return tuple(factory(item, f"traceability_ledger.entities.{name}[{i}]") for i, item in enumerate(_array(e[name], f"traceability_ledger.entities.{name}")))
    def judge(v: Any, w: str) -> JudgeBinding: return JudgeBinding(*_two_ids(v, "obligation_id", "judge_id", w))
    def bundle(v: Any, w: str) -> EvidenceBundle: return EvidenceBundle(*_two_ids(v, "id", "lane_id", w))
    def member(v: Any, w: str) -> EvidenceMembership: return EvidenceMembership(*_two_ids(v, "bundle_id", "evidence_source_id", w))
    def context(v: Any, w: str) -> LaneContext: return LaneContext(*_two_ids(v, "lane_id", "artifact_type", w))
    def output(v: Any, w: str) -> LaneOutput: return LaneOutput(*_two_ids(v, "lane_id", "artifact_type", w))
    def gate(v: Any, w: str) -> GateRecord: return GateRecord(*_two_ids(v, "lane_id", "gate_id", w))
    def pair(v: Any, w: str) -> ObligationGatePair:
        m = _record(v, ("lane_id", "obligation_id", "gate_id"), w)
        return ObligationGatePair(*(require_identifier(m[n], f"{w}.{n}") for n in ("lane_id", "obligation_id", "gate_id")))
    def negative(cls: Any):
        return lambda v, w: cls(*_two_ids(v, "owner_id", "id", w))
    entities = LedgerEntities(
        judge_ids=_identifier_array(e["judge_ids"], "traceability_ledger.entities.judge_ids"),
        obligation_to_judge=records("obligation_to_judge", judge), evidence_bundles=records("evidence_bundles", bundle),
        evidence_source_ids=_identifier_array(e["evidence_source_ids"], "traceability_ledger.entities.evidence_source_ids"),
        evidence_bundle_memberships=records("evidence_bundle_memberships", member), lane_contexts=records("lane_contexts", context),
        post_gate_outputs=records("post_gate_outputs", output), gates=records("gates", gate), obligation_gate_pairs=records("obligation_gate_pairs", pair),
        judge_negative_tests=records("judge_negative_tests", negative(JudgeNegativeBinding)),
        aggregate_negative_tests=records("aggregate_negative_tests", negative(AggregateNegativeBinding)),
        plumbing_negative_tests=records("plumbing_negative_tests", negative(PlumbingNegativeBinding)),
    )
    return TraceabilityLedgerValue(SCHEMA_VERSION, require_identifier(root["id"], "traceability_ledger.id"), PROFILE_ID, require_sha256(root["design_gate_revision"], "traceability_ledger.design_gate_revision"), predecessor, Cardinalities(**cardinality_values), DigestBindings(**bind_values), entities)


def parse_traceability_ledger(envelope: TraceabilityLedger) -> ParsedJson:
    """Stage-1 parse while retaining duplicate keys and declaration order."""
    return parse_json_object(envelope.ledger_ref.buffer, LOGICAL_NAME)
