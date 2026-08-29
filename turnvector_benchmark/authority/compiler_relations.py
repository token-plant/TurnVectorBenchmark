"""Semantic relation validation and derivation for CoverageCompiler.

This module owns the validation-only Phase B sweep (stages 29--40), the
Phase C relation derivation, the 27 canonical digest streams, and canonical
expected-key enumeration.  It deliberately accepts the authority package's
immutable value objects by protocol rather than importing their parsers; this
keeps the relation layer acyclic and also makes its narrow helpers testable.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from turnvector_benchmark.compile_limits import CompileLimits, checked_add, checked_mul

from .errors import CompilerInternalError, CONTRACT_FAILURE_MESSAGES


LANE_ORDER = (
    "core-event-replay", "scheduler-policy", "scheduler-performance",
    "request-serving-lifecycle", "mlx-native-correctness", "bounded-turn-and-ffi",
    "residency-and-memory-governor", "cross-model-serving",
    "observability-qualification", "persistence-and-recovery",
    "protocol-and-owner-lifecycle", "certification-envelopes",
)
LANE_OBLIGATION_COUNTS = (4, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4)
LANE_CASE_COUNTS = (20, 3, 40, 15, 18, 36, 80, 36, 36, 22, 24, 95)
LANE_GATE_COUNTS = (3, 3, 4, 5, 4, 4, 7, 7, 7, 4, 5, 5)
LANE_RAW_COUNTS = (3, 1, 4, 4, 5, 4, 5, 5, 6, 7, 4, 4)
LANE_CONTEXT_COUNTS = (1, 2, 2, 1, 2, 2, 2, 2, 2, 1, 2, 2)
LANE_OUTPUT_COUNTS = (1, 2, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_UR = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._")
_HEX_UPPER = frozenset(b"0123456789ABCDEF")
_MISSING = object()


@dataclass(frozen=True)
class RelationValidationError(Exception):
    """A deterministic stage-29--40 ContractFailure precursor."""

    variant: str
    pointers: Tuple[str, ...]

    @property
    def message(self) -> str:
        return CONTRACT_FAILURE_MESSAGES[self.variant]

    @property
    def reason_code(self) -> str:
        return self.variant

    @property
    def diagnostics(self) -> Tuple[str, ...]:
        return tuple("%s|%s|%s" % (self.variant, p, self.variant) for p in self.pointers)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class _Lane:
    lane_id: str
    cases: Tuple[str, ...]
    case_ids: Tuple[str, ...]
    gates: Tuple[str, ...]
    required_artifacts: Tuple[str, ...]


@dataclass(frozen=True)
class _Obligation:
    obligation_id: str
    lane_id: str
    behavior_case_id: str
    required: bool
    ordinal: int


@dataclass(frozen=True)
class _Pair:
    lane_id: str
    obligation_id: str
    gate_id: str
    index: int


@dataclass(frozen=True)
class PhaseBResult:
    """Validated, derivation-ready state; contains no derived relation record."""

    lanes: Tuple[_Lane, ...]
    obligations: Tuple[_Obligation, ...]
    all_obligations: Tuple[_Obligation, ...]
    judges: Tuple[str, ...]
    obligation_judges: Tuple[Tuple[str, str], ...]
    bundles: Tuple[Tuple[str, str], ...]  # (lane, bundle)
    evidence_sources: Tuple[str, ...]
    bundle_evidence: Tuple[Tuple[str, str], ...]
    lane_contexts: Tuple[Tuple[str, str], ...]
    lane_outputs: Tuple[Tuple[str, str], ...]
    gates: Tuple[Tuple[str, str], ...]
    pairs: Tuple[_Pair, ...]
    judge_templates: Tuple[Tuple[str, str], ...]  # (owner, template)
    aggregate_templates: Tuple[Tuple[str, str], ...]
    plumbing_templates: Tuple[Tuple[str, str], ...]
    cardinalities: Mapping[str, int]
    m: int
    h: int
    r_he: int
    k_exec: int


@dataclass(frozen=True)
class EnforcementPath:
    path_id: str
    obligation_id: str
    case_id: str
    judge_id: str
    bundle_id: str
    gate_id: str


@dataclass(frozen=True)
class DerivedRelations:
    gate_aggregate_negative: Tuple[Tuple[str, str], ...]
    gate_plumbing_negative: Tuple[Tuple[str, str], ...]
    enforcement_paths: Tuple[EnforcementPath, ...]
    path_case: Tuple[Tuple[str, str], ...]
    path_gate: Tuple[Tuple[str, str], ...]
    path_judge_negative: Tuple[Tuple[str, str], ...]
    path_evidence: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class DigestStreams:
    entity_sha256: Mapping[str, str]
    relation_sha256: Mapping[str, str]
    byte_count: int
    value_byte_count: int


@dataclass(frozen=True)
class ExpectedKeys:
    names: Tuple[str, ...]
    sha256: str
    count: int
    compile_key_count: int
    execution_key_count: int
    run_global_key_count: int
    preimage_byte_count: int
    names_array_byte_count: int


@dataclass(frozen=True)
class CompiledRelations:
    validated: PhaseBResult
    derived: DerivedRelations
    digests: DigestStreams
    expected_keys: ExpectedKeys


def _get(value: Any, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    if default is not _MISSING:
        return default
    raise CompilerInternalError("relation input lacks field %s" % "/".join(names))


def _value(value: Any) -> Any:
    if isinstance(value, Mapping) and "value" in value:
        return value["value"]
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def _seq(value: Any, *names: str) -> Tuple[Any, ...]:
    found = _get(value, *names, default=())
    return tuple(found)


def _record_tuple(record: Any, fields: Sequence[Sequence[str]]) -> Tuple[str, ...]:
    return tuple(str(_get(record, *aliases)) for aliases in fields)


def _fail(variant: str, *pointers: str) -> None:
    # ContractFailure owns the 64-diagnostic cap; relation sweeps never emit more.
    raise RelationValidationError(variant, tuple(pointers[:64]))


def pct_encode(text: str) -> str:
    """Return the minimal uppercase percent encoding of UTF-8 *text*."""
    if not isinstance(text, str):
        raise ValueError("component must be text")
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("component contains an unpaired surrogate") from error
    return "".join(chr(b) if b in _UR else "%%%02X" % b for b in raw)


def pct_decode(component: str) -> str:
    """Strictly invert :func:`pct_encode`, rejecting every noncanonical form."""
    if not isinstance(component, str):
        raise ValueError("component must be text")
    try:
        source = component.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("encoded component must be ASCII") from error
    out = bytearray()
    index = 0
    while index < len(source):
        byte = source[index]
        if byte == 0x25:
            if index + 2 >= len(source):
                raise ValueError("stray percent escape")
            digits = source[index + 1:index + 3]
            if digits[0] not in _HEX_UPPER or digits[1] not in _HEX_UPPER:
                raise ValueError("percent escape must use uppercase hex")
            decoded = int(digits.decode("ascii"), 16)
            if decoded in _UR:
                raise ValueError("nonminimal percent escape")
            out.append(decoded)
            index += 3
        else:
            if byte not in _UR:
                raise ValueError("reserved byte must be percent encoded")
            out.append(byte)
            index += 1
    try:
        text = bytes(out).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("percent escapes do not encode valid UTF-8") from error
    if pct_encode(text) != component:
        raise ValueError("component is not canonical")
    return text


def _decode_identifier(component: str) -> str:
    value = pct_decode(component)
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("decoded component is not an identifier")
    return value


def encode_enforcement_path(obligation_id: str, case_id: str, judge_id: str,
                            bundle_id: str, gate_id: str) -> str:
    values = (obligation_id, case_id, judge_id, bundle_id, gate_id)
    if any(_IDENTIFIER.fullmatch(item) is None for item in values):
        raise ValueError("enforcement-path fields must be identifiers")
    return "~".join(pct_encode(item) for item in values)


def decode_enforcement_path(component: str) -> Tuple[str, str, str, str, str]:
    pieces = component.split("~")
    if len(pieces) != 5:
        raise ValueError("enforcement path must contain exactly five fields")
    values = tuple(_decode_identifier(piece) for piece in pieces)
    if encode_enforcement_path(*values) != component:
        raise ValueError("enforcement path is not canonical")
    return values  # type: ignore[return-value]


def _matrix_count(matrix: Any) -> int:
    product = 1
    for dimension in _seq(matrix, "dimensions"):
        product = checked_mul(product, len(_seq(dimension, "values")), "case-plan product")
    return product


def _normalize(catalog: Any, expectation: Any, ledger: Any) -> Tuple[
        Tuple[_Lane, ...], Tuple[_Obligation, ...], Tuple[_Obligation, ...], Any, Mapping[str, int]]:
    expectation = _value(expectation)
    catalog = _value(catalog)
    ledger = _value(ledger)
    lanes: List[_Lane] = []
    for lane in _seq(expectation, "lanes"):
        lane_id = str(_get(lane, "lane_id", "id"))
        ordinal = 1
        expanded: List[str] = []
        for matrix in _seq(lane, "matrices"):
            matrix_id = str(_get(matrix, "matrix_id", "id"))
            count = _matrix_count(matrix)
            for _ in range(count):
                expanded.append("%s.%s.%04d" % (lane_id, matrix_id, ordinal))
                ordinal += 1
        cases = tuple(str(_get(case, "case_id", "id")) for case in _seq(lane, "cases"))
        gates = tuple(str(_get(gate, "gate_id", "id")) for gate in _seq(lane, "gates"))
        lanes.append(_Lane(lane_id, cases, tuple(expanded), gates,
                           tuple(str(x) for x in _seq(lane, "required_artifacts"))))
    raw_obligations = _seq(catalog, "obligations", "records")
    all_obligations = tuple(
        _Obligation(str(_get(item, "obligation_id", "id")),
                    str(_get(item, "lane_id")), str(_get(item, "behavior_case_id")),
                    bool(_get(item, "required")), index + 1)
        for index, item in enumerate(raw_obligations)
    )
    obligations = tuple(item for item in all_obligations if item.required)
    entities = _get(ledger, "entities", default=ledger)
    cardinalities = _get(ledger, "cardinalities")
    if not isinstance(cardinalities, Mapping):
        cardinalities = {
            name: getattr(cardinalities, name)
            for name in getattr(cardinalities, "__dataclass_fields__", {})
        }
    return tuple(lanes), obligations, all_obligations, entities, cardinalities


def _later_probes(lanes: Tuple[_Lane, ...], obligations: Tuple[_Obligation, ...],
                  judges: Tuple[str, ...], bundles: Tuple[Tuple[str, str], ...],
                  sources: Tuple[str, ...], memberships: Tuple[Tuple[str, str], ...],
                  contexts: Tuple[Tuple[str, str], ...], outputs: Tuple[Tuple[str, str], ...],
                  gates: Tuple[Tuple[str, str], ...], pairs: Tuple[_Pair, ...],
                  judge_templates: Tuple[Tuple[str, str], ...],
                  aggregate_templates: Tuple[Tuple[str, str], ...],
                  plumbing_templates: Tuple[Tuple[str, str], ...], limits: CompileLimits
                  ) -> Tuple[Optional[RelationValidationError], Optional[RelationValidationError], Optional[RelationValidationError]]:
    # Stage 38, parsed-property caps at sweep position zero.
    bundle_degree: Dict[str, int] = {}
    for index, (bundle_id, _source_id) in enumerate(memberships):
        bundle_degree[bundle_id] = bundle_degree.get(bundle_id, 0) + 1
        if bundle_degree[bundle_id] > limits.evidence_members_per_bundle_or_path_max:
            issue38 = RelationValidationError("traceability_degree_cap_exceeded",
                ("/traceability_ledger/entities/evidence_bundle_memberships/%d" % index,))
            break
    else:
        issue38 = None
    for records, maximum, name in (
        (contexts, limits.context_artifacts_per_lane_max, "lane_contexts"),
        (outputs, limits.post_gate_outputs_per_lane_max, "post_gate_outputs"),
    ):
        degrees: Dict[str, int] = {}
        for index, (lane_id, _kind) in enumerate(records):
            degrees[lane_id] = degrees.get(lane_id, 0) + 1
            if issue38 is None and degrees[lane_id] > maximum:
                issue38 = RelationValidationError("traceability_degree_cap_exceeded",
                    ("/traceability_ledger/entities/%s/%d" % (name, index),))
                break
    count_caps = (
        (sum(len(lane.case_ids) for lane in lanes), limits.case_plan_count_max, "case_plan_count"),
        (len(obligations), limits.obligation_count_max, "obligation_count"),
        (len(judges), limits.judge_count_max, "judge_count"),
        (len(bundles), limits.evidence_bundle_count_max, "evidence_bundle_count"),
        (len(sources), limits.evidence_source_count_max, "evidence_source_count"),
        (len(gates), limits.gate_count_max, "gate_count"),
    )
    if issue38 is None:
        for actual, maximum, field in count_caps:
            if actual > maximum:
                issue38 = RelationValidationError("traceability_degree_cap_exceeded",
                    ("/traceability_ledger/cardinalities/%s" % field,))
                break
    if issue38 is None:
        for records, field in ((judge_templates, "judge_negative_template_count"),
                               (aggregate_templates, "aggregate_negative_template_count"),
                               (plumbing_templates, "plumbing_negative_template_count")):
            if len(records) > limits.negative_test_class_count_max:
                issue38 = RelationValidationError("traceability_degree_cap_exceeded",
                    ("/traceability_ledger/cardinalities/%s" % field,))
                break
    if issue38 is None:
        cases_by_lane = {lane.lane_id: lane.case_ids for lane in lanes}
        obligation_degree: Dict[str, int] = {}
        case_degree: Dict[str, int] = {}
        gate_degree: Dict[str, int] = {}
        path_count = 0
        for pair in pairs:
            for case_id in sorted(cases_by_lane.get(pair.lane_id, ())):
                candidate = path_count + 1
                od = obligation_degree.get(pair.obligation_id, 0) + 1
                cd = case_degree.get(case_id, 0) + 1
                gd = gate_degree.get(pair.gate_id, 0) + 1
                if (candidate > limits.path_count_max or
                        od > limits.paths_per_obligation_max or
                        cd > limits.paths_per_case_max or gd > limits.paths_per_gate_max):
                    issue38 = RelationValidationError("traceability_degree_cap_exceeded",
                        ("/traceability_ledger/entities/obligation_gate_pairs/%d" % pair.index,))
                    break
                path_count = candidate
                obligation_degree[pair.obligation_id] = od
                case_degree[case_id] = cd
                gate_degree[pair.gate_id] = gd
            if issue38 is not None:
                break

    # Stage 39 exact frozen base-domain order.
    issue39: Optional[RelationValidationError] = None
    if not lanes:
        issue39 = RelationValidationError("traceability_empty_required_set", ("/benchmark_expectation/lanes",))
    elif not obligations:
        issue39 = RelationValidationError("traceability_empty_required_set", ("/obligation_catalog",))
    else:
        for index, lane in enumerate(lanes):
            if not lane.cases:
                issue39 = RelationValidationError("traceability_empty_required_set",
                    ("/benchmark_expectation/lanes/%d/cases" % index,)); break
            if not lane.case_ids:
                issue39 = RelationValidationError("traceability_empty_required_set",
                    ("/benchmark_expectation/lanes/%d/matrices" % index,)); break
    if issue39 is None:
        checks = ((judges, "/traceability_ledger/entities/judge_ids"),
                  (gates, "/traceability_ledger/entities/gates"),
                  (bundles, "/traceability_ledger/entities/evidence_bundles"),
                  (sources, "/traceability_ledger/entities/evidence_source_ids"),
                  (contexts, "/traceability_ledger/entities/lane_contexts"),
                  (outputs, "/traceability_ledger/entities/post_gate_outputs"))
        for records, pointer in checks:
            if not records:
                issue39 = RelationValidationError("traceability_empty_required_set", (pointer,)); break

    # Stage 40: counts, judge owner order, then gate order with interleaving.
    issue40: Optional[RelationValidationError] = None
    for records, expected, field in (
        (judge_templates, 46, "judge_negative_template_count"),
        (aggregate_templates, 58, "aggregate_negative_template_count"),
        (plumbing_templates, 58, "plumbing_negative_template_count"),
    ):
        if len(records) != expected:
            issue40 = RelationValidationError("traceability_negative_test_coverage_missing",
                ("/traceability_ledger/cardinalities/%s" % field,)); break
    if issue40 is None:
        judge_map = _owner_map(judge_templates)
        owner_order: List[str] = []
        seen = set()
        for pair in pairs:
            if pair.obligation_id not in seen:
                seen.add(pair.obligation_id); owner_order.append(pair.obligation_id)
        for owner in owner_order:
            records = judge_map.get(owner, ())
            if len(records) != 1:
                pointer = "/traceability_ledger/entities/judge_negative_tests"
                if len(records) > 1:
                    pointer += "/%d" % records[1][0]
                issue40 = RelationValidationError("traceability_negative_test_coverage_missing", (pointer,)); break
    if issue40 is None:
        aggregate_map = _owner_map(aggregate_templates)
        plumbing_map = _owner_map(plumbing_templates)
        for _lane, gate_id in sorted(gates):
            for records, name in ((aggregate_map.get(gate_id, ()), "aggregate_negative_tests"),
                                  (plumbing_map.get(gate_id, ()), "plumbing_negative_tests")):
                if len(records) != 1:
                    pointer = "/traceability_ledger/entities/%s" % name
                    if len(records) > 1:
                        pointer += "/%d" % records[1][0]
                    issue40 = RelationValidationError("traceability_negative_test_coverage_missing", (pointer,)); break
            if issue40 is not None:
                break
    return issue38, issue39, issue40


def _owner_map(records: Tuple[Tuple[str, str], ...]) -> Dict[str, Tuple[Tuple[int, str], ...]]:
    values: Dict[str, List[Tuple[int, str]]] = {}
    for index, (owner, template) in enumerate(records):
        values.setdefault(owner, []).append((index, template))
    return {owner: tuple(items) for owner, items in values.items()}


def validate_relations(catalog: Any, expectation: Any, ledger: Any,
                       limits: Optional[CompileLimits] = None) -> PhaseBResult:
    """Run the exact validation-only Phase B sweep and return K2 state.

    No CasePlan or derived relation record is materialized by this function.
    A failure raises :class:`RelationValidationError` with the frozen variant
    and JSON pointer; checked arithmetic remains owned by stages 41/42 and is
    intentionally allowed to propagate to the pipeline dispatcher.
    """
    if limits is None:
        limits = CompileLimits.frozen()
    lanes, obligations, all_obligations, entities, cardinalities = _normalize(
        catalog, expectation, ledger)
    judges = tuple(str(x) for x in _seq(entities, "judge_ids"))
    obligation_judges = tuple(_record_tuple(x, (("obligation_id",), ("judge_id",)))
                               for x in _seq(entities, "obligation_to_judge"))
    bundles = tuple((str(_get(x, "lane_id")), str(_get(x, "bundle_id", "id")))
                    for x in _seq(entities, "evidence_bundles"))
    sources = tuple(str(x) for x in _seq(entities, "evidence_source_ids"))
    memberships = tuple(_record_tuple(x, (("bundle_id",), ("evidence_source_id",)))
                        for x in _seq(entities, "evidence_bundle_memberships"))
    contexts = tuple(_record_tuple(x, (("lane_id",), ("artifact_type",)))
                     for x in _seq(entities, "lane_contexts"))
    outputs = tuple(_record_tuple(x, (("lane_id",), ("artifact_type",)))
                    for x in _seq(entities, "post_gate_outputs"))
    gates = tuple(_record_tuple(x, (("lane_id",), ("gate_id",)))
                  for x in _seq(entities, "gates"))
    pairs = tuple(_Pair(str(_get(x, "lane_id")), str(_get(x, "obligation_id")),
                        str(_get(x, "gate_id")), index)
                  for index, x in enumerate(_seq(entities, "obligation_gate_pairs")))
    judge_templates = tuple(_record_tuple(x, (("owner_id",), ("id",)))
                            for x in _seq(entities, "judge_negative_tests"))
    aggregate_templates = tuple(_record_tuple(x, (("owner_id",), ("id",)))
                                for x in _seq(entities, "aggregate_negative_tests"))
    plumbing_templates = tuple(_record_tuple(x, (("owner_id",), ("id",)))
                               for x in _seq(entities, "plumbing_negative_tests"))

    lane_ids = {x.lane_id for x in lanes}; lane_cases = {x.lane_id: set(x.cases) for x in lanes}
    obligation_ids = {x.obligation_id for x in obligations}; judge_ids = set(judges)
    bundle_ids = {bundle for _lane, bundle in bundles}; source_ids = set(sources)
    gate_ids = {gate for _lane, gate in gates}

    # Stage 29 -- all reference domains, in frozen input/array order.
    for item in all_obligations:
        if item.lane_id not in lane_ids or item.behavior_case_id not in lane_cases.get(item.lane_id, set()):
            _fail("traceability_unknown_entity", "/obligation_catalog/%d/behavior_case_id" % item.ordinal)
    checks29 = (
        (obligation_judges, (obligation_ids, judge_ids), "obligation_to_judge",
         ("obligation_id", "judge_id")),
        (bundles, (lane_ids, None), "evidence_bundles", ("lane_id", "id")),
        (memberships, (bundle_ids, source_ids), "evidence_bundle_memberships",
         ("bundle_id", "evidence_source_id")),
        (contexts, (lane_ids, None), "lane_contexts", ("lane_id", "artifact_type")),
        (outputs, (lane_ids, None), "post_gate_outputs", ("lane_id", "artifact_type")),
        (gates, (lane_ids, None), "gates", ("lane_id", "gate_id")),
    )
    for records, domains, name, fields in checks29:
        for index, record in enumerate(records):
            for position, domain in enumerate(domains):
                if domain is not None and record[position] not in domain:
                    _fail("traceability_unknown_entity",
                          "/traceability_ledger/entities/%s/%d/%s" % (name, index, fields[position]))
    for pair in pairs:
        for value, domain, field in ((pair.lane_id, lane_ids, "lane_id"),
                                     (pair.obligation_id, obligation_ids, "obligation_id"),
                                     (pair.gate_id, gate_ids, "gate_id")):
            if value not in domain:
                _fail("traceability_unknown_entity",
                      "/traceability_ledger/entities/obligation_gate_pairs/%d/%s" % (pair.index, field))
    for records, domain, name in ((judge_templates, obligation_ids, "judge_negative_tests"),
                                  (aggregate_templates, gate_ids, "aggregate_negative_tests"),
                                  (plumbing_templates, gate_ids, "plumbing_negative_tests")):
        for index, (owner, _template) in enumerate(records):
            if owner not in domain:
                _fail("traceability_unknown_entity",
                      "/traceability_ledger/entities/%s/%d/owner_id" % (name, index))

    # Stage 30 -- complete records only; negative-template arrays are excluded.
    duplicate_arrays = (
        (obligation_judges, "obligation_to_judge"), (bundles, "evidence_bundles"),
        (memberships, "evidence_bundle_memberships"), (contexts, "lane_contexts"),
        (outputs, "post_gate_outputs"), (gates, "gates"),
        (tuple((x.lane_id, x.obligation_id, x.gate_id) for x in pairs), "obligation_gate_pairs"),
    )
    for records, name in duplicate_arrays:
        seen = set()
        for index, record in enumerate(records):
            if record in seen:
                _fail("traceability_duplicate_entity",
                      "/traceability_ledger/entities/%s/%d" % (name, index))
            seen.add(record)

    by_obligation: Dict[str, List[str]] = {}
    by_judge: Dict[str, List[str]] = {}
    for obligation_id, judge_id in obligation_judges:
        by_obligation.setdefault(obligation_id, []).append(judge_id)
        by_judge.setdefault(judge_id, []).append(obligation_id)
    pair_obligations = {x.obligation_id for x in pairs}; pair_gates = {x.gate_id for x in pairs}
    # Stages 31--36, with the exact empty-base skips.
    if obligations and judges and gates:
        for item in obligations:
            if not by_obligation.get(item.obligation_id) or item.obligation_id not in pair_obligations:
                _fail("traceability_orphan_obligation", "/obligation_catalog/%d" % item.ordinal)
    if obligations:
        required_pairs = {(x.lane_id, x.behavior_case_id) for x in obligations}
        for lane_index, lane in enumerate(lanes):
            for case_index, case_id in enumerate(lane.cases):
                if (lane.lane_id, case_id) not in required_pairs:
                    _fail("traceability_orphan_case",
                          "/benchmark_expectation/lanes/%d/cases/%d/id" % (lane_index, case_index))
    if judges:
        for index, judge_id in enumerate(judges):
            if not by_judge.get(judge_id):
                _fail("traceability_orphan_judge", "/traceability_ledger/entities/judge_ids/%d" % index)
    membership_bundles = {x[0] for x in memberships}; bundle_lanes = {x[0] for x in bundles}
    if bundles:
        for index, (_lane, bundle) in enumerate(bundles):
            if bundle not in membership_bundles:
                _fail("traceability_orphan_bundle",
                      "/traceability_ledger/entities/evidence_bundles/%d" % index)
        for lane_index, lane in enumerate(lanes):
            if lane.lane_id not in bundle_lanes:
                _fail("traceability_orphan_bundle", "/benchmark_expectation/lanes/%d/id" % lane_index)
    if gates:
        for index, (_lane, gate_id) in enumerate(gates):
            if gate_id not in pair_gates:
                _fail("traceability_orphan_gate", "/traceability_ledger/entities/gates/%d/gate_id" % index)
    membership_sources = {x[1] for x in memberships}
    if sources:
        for index, source_id in enumerate(sources):
            if source_id not in membership_sources:
                _fail("traceability_orphan_evidence",
                      "/traceability_ledger/entities/evidence_source_ids/%d" % index)

    issue38, issue39, issue40 = _later_probes(
        lanes, obligations, judges, bundles, sources, memberships, contexts, outputs,
        gates, pairs, judge_templates, aggregate_templates, plumbing_templates, limits)

    # Stream the skeleton counters without retaining a path or CasePlan record.
    lane_case_counts = tuple(len(x.case_ids) for x in lanes)
    lane_obligation_counts = tuple(sum(1 for o in obligations if o.lane_id == lane.lane_id) for lane in lanes)
    lane_gate_counts = tuple(sum(1 for item in gates if item[0] == lane.lane_id) for lane in lanes)
    lane_raw_counts: List[int] = []
    lane_context_counts = tuple(sum(1 for item in contexts if item[0] == lane.lane_id) for lane in lanes)
    lane_output_counts = tuple(sum(1 for item in outputs if item[0] == lane.lane_id) for lane in lanes)
    bundle_by_lane: Dict[str, List[str]] = {}
    for lane_id, bundle_id in bundles:
        bundle_by_lane.setdefault(lane_id, []).append(bundle_id)
    evidence_by_bundle: Dict[str, List[str]] = {}
    for bundle_id, source_id in memberships:
        evidence_by_bundle.setdefault(bundle_id, []).append(source_id)
    for lane in lanes:
        lane_raw_counts.append(sum(len(evidence_by_bundle.get(b, ())) for b in bundle_by_lane.get(lane.lane_id, ())))
    m_by_lane = tuple(sum(1 for p in pairs if p.lane_id == lane.lane_id) for lane in lanes)
    m = 0; h = 0; r_he = 0
    for lane, pair_count, case_count in zip(lanes, m_by_lane, lane_case_counts):
        m = checked_add(m, pair_count, "obligation-gate pair count")
        lane_h = checked_mul(pair_count, case_count, "enforcement-path count")
        h = checked_add(h, lane_h, "enforcement-path count")
        degree = sum(len(evidence_by_bundle.get(b, ())) for b in bundle_by_lane.get(lane.lane_id, ()))
        r_he = checked_add(r_he, checked_mul(lane_h, degree, "path-evidence count"),
                           "path-evidence count")
    k_exec = checked_add(425 + len(contexts) + len(outputs) + 4 * len(gates),
                         checked_add(r_he, 3 * h, "execution key count"), "execution key count")

    # Stage 37 is deliberately skipped if a stage-38/39/40 predicate exists.
    if issue38 is None and issue39 is None and issue40 is None:
        repeated = set()
        for item in all_obligations:
            key = (item.lane_id, item.behavior_case_id)
            if key in repeated:
                _fail("traceability_relation_mismatch",
                      "/obligation_catalog/%d/behavior_case_id" % item.ordinal)
            repeated.add(key)
        obligation_lane = {x.obligation_id: x.lane_id for x in obligations}
        gate_lane = {gate: lane for lane, gate in gates}
        for pair in pairs:
            if obligation_lane[pair.obligation_id] != pair.lane_id or gate_lane[pair.gate_id] != pair.lane_id:
                _fail("traceability_relation_mismatch",
                      "/traceability_ledger/entities/obligation_gate_pairs/%d/lane_id" % pair.index)
        # Actual values in the exact 27-field cardinality declaration order.
        entity_actual = (len(lanes) + sum(len(x.cases) for x in lanes) + sum(lane_case_counts) +
                         len(obligations) + len(judges) + len(bundles) + len(sources) +
                         len({x[1] for x in contexts}) + len({x[1] for x in outputs}) +
                         len(gates) + len(judge_templates) + len(aggregate_templates) +
                         len(plumbing_templates) + h)
        q_total = checked_add(checked_add(4 * h, r_he), m + 315, "relation total")
        r_total = checked_add(checked_add(11 * h, 2 * r_he), 2 * m + 630, "endpoint total")
        actual = (
            ("lane_count", len(lanes)), ("case_plan_count", sum(lane_case_counts)),
            ("gate_count", len(gates)), ("obligation_count", len(obligations)),
            ("judge_count", len(judges)), ("evidence_bundle_count", len(bundles)),
            ("evidence_source_count", len(sources)), ("lane_context_membership_count", len(contexts)),
            ("post_gate_output_membership_count", len(outputs)),
            ("artifact_membership_total", len(memberships) + len(contexts) + len(outputs)),
            ("judge_negative_template_count", len(judge_templates)),
            ("aggregate_negative_template_count", len(aggregate_templates)),
            ("plumbing_negative_template_count", len(plumbing_templates)),
            ("lane_obligation_counts", lane_obligation_counts), ("lane_case_counts", lane_case_counts),
            ("lane_gate_counts", lane_gate_counts), ("lane_raw_membership_counts", tuple(lane_raw_counts)),
            ("lane_context_counts", lane_context_counts), ("lane_output_counts", lane_output_counts),
            ("obligation_gate_pair_count", m), ("enforcement_path_count", h),
            ("evidence_membership_record_count", len(memberships)),
            ("path_evidence_record_count", r_he), ("endpoint_reference_count", r_total),
            ("relation_record_count", q_total), ("entity_record_count", entity_actual),
            ("key_count_exec", k_exec),
        )
        for field, expected in actual:
            observed = cardinalities.get(field, _MISSING)
            if observed is _MISSING or tuple(observed) != expected if isinstance(expected, tuple) else observed != expected:
                _fail("traceability_relation_mismatch", "/traceability_ledger/cardinalities/%s" % field)
        shape_drift = (len(obligation_judges) != 46 or len(judges) != 46 or len(obligations) != 46 or
                       any(len(by_obligation.get(x.obligation_id, ())) != 1 for x in obligations) or
                       any(len(by_judge.get(x, ())) != 1 for x in judges))
        if shape_drift:
            _fail("traceability_relation_mismatch",
                  "/traceability_ledger/cardinalities/obligation_count")
        if tuple(x.lane_id for x in lanes) != LANE_ORDER:
            _fail("traceability_relation_mismatch", "/traceability_ledger/cardinalities/lane_count")
        if (lane_obligation_counts != LANE_OBLIGATION_COUNTS or lane_case_counts != LANE_CASE_COUNTS or
                lane_gate_counts != LANE_GATE_COUNTS or tuple(lane_raw_counts) != LANE_RAW_COUNTS or
                lane_context_counts != LANE_CONTEXT_COUNTS or lane_output_counts != LANE_OUTPUT_COUNTS):
            _fail("traceability_relation_mismatch", "/traceability_ledger/cardinalities/lane_case_counts")
        if tuple(tuple(sorted(x.gates)) for x in lanes) != tuple(
                tuple(sorted(g for lane_id, g in gates if lane_id == lane.lane_id)) for lane in lanes):
            _fail("traceability_relation_mismatch", "/traceability_ledger/entities/gates")
        # Exactly one bundle per lane and exact required-artifact causal partition.
        for lane in lanes:
            if len(bundle_by_lane.get(lane.lane_id, ())) != 1:
                _fail("traceability_relation_mismatch", "/traceability_ledger/entities/evidence_bundles")
            required = set(lane.required_artifacts)
            context_set = {kind for lane_id, kind in contexts if lane_id == lane.lane_id}
            output_set = {kind for lane_id, kind in outputs if lane_id == lane.lane_id}
            if not context_set.issubset(required) or not output_set.issubset(required):
                _fail("traceability_relation_mismatch", "/traceability_ledger/entities/lane_contexts")
            if len(required - context_set - output_set) != lane_raw_counts[LANE_ORDER.index(lane.lane_id)]:
                _fail("traceability_relation_mismatch", "/traceability_ledger/cardinalities/artifact_membership_total")

    if issue38 is not None:
        raise issue38
    if issue39 is not None:
        raise issue39
    if issue40 is not None:
        raise issue40
    return PhaseBResult(lanes, obligations, all_obligations, judges, obligation_judges,
                        bundles, sources, memberships, contexts, outputs, gates, pairs,
                        judge_templates, aggregate_templates, plumbing_templates,
                        cardinalities, m, h, r_he, k_exec)


def _allocation(hook: Optional[Callable[[str, int], None]], label: str, charge: int) -> None:
    if hook is None:
        return
    if callable(hook):
        hook(label, charge)
    elif hasattr(hook, "retain_derived"):
        hook.retain_derived(label, charge)  # type: ignore[attr-defined]
    else:
        raise CompilerInternalError("unsupported derivation allocation hook")


def derive_relations(validated: PhaseBResult,
                     allocation_hook: Optional[Callable[[str, int], None]] = None) -> DerivedRelations:
    """Run Phase C in its frozen allocation/counter order."""
    aggregate = {owner: template for owner, template in validated.aggregate_templates}
    plumbing = {owner: template for owner, template in validated.plumbing_templates}
    judge_template = {owner: template for owner, template in validated.judge_templates}
    judges = dict(validated.obligation_judges)
    bundles = {lane: bundle for lane, bundle in validated.bundles}
    evidence: Dict[str, List[str]] = {}
    for bundle, source in validated.bundle_evidence:
        evidence.setdefault(bundle, []).append(source)
    r_gng: List[Tuple[str, str]] = []
    r_gnp: List[Tuple[str, str]] = []
    for _lane, gate in sorted(validated.gates):
        _allocation(allocation_hook, "gate_aggregate_negative", 24)
        r_gng.append((gate, aggregate[gate]))
        _allocation(allocation_hook, "gate_plumbing_negative", 24)
        r_gnp.append((gate, plumbing[gate]))
    paths: List[EnforcementPath] = []
    r_ch: List[Tuple[str, str]] = []
    r_gh: List[Tuple[str, str]] = []
    r_hnj: List[Tuple[str, str]] = []
    r_he: List[Tuple[str, str]] = []
    pairs_by_lane: Dict[str, List[_Pair]] = {}
    for pair in validated.pairs:
        pairs_by_lane.setdefault(pair.lane_id, []).append(pair)
    for lane in validated.lanes:
        bundle = bundles[lane.lane_id]
        for pair in sorted(pairs_by_lane.get(lane.lane_id, ()),
                           key=lambda x: (x.lane_id, x.obligation_id, x.gate_id)):
            judge = judges[pair.obligation_id]
            for case_id in sorted(lane.case_ids):
                path_id = encode_enforcement_path(pair.obligation_id, case_id, judge,
                                                  bundle, pair.gate_id)
                path = EnforcementPath(path_id, pair.obligation_id, case_id, judge,
                                       bundle, pair.gate_id)
                _allocation(allocation_hook, "enforcement_path", 48 + len(path_id.encode("utf-8")))
                paths.append(path)
                _allocation(allocation_hook, "path_case", 24); r_ch.append((path_id, case_id))
                _allocation(allocation_hook, "path_gate", 24); r_gh.append((path_id, pair.gate_id))
                _allocation(allocation_hook, "path_judge_negative", 24)
                r_hnj.append((path_id, judge_template[pair.obligation_id]))
                for source in sorted(evidence.get(bundle, ())):
                    _allocation(allocation_hook, "path_evidence", 24)
                    r_he.append((path_id, source))
    if len(paths) != validated.h or len(r_he) != validated.r_he:
        raise CompilerInternalError("Phase C relation count diverged from Phase B")
    return DerivedRelations(tuple(r_gng), tuple(r_gnp), tuple(paths), tuple(r_ch),
                            tuple(r_gh), tuple(r_hnj), tuple(r_he))


def _json_line(record: Mapping[str, str]) -> bytes:
    return (json.dumps(dict(record), sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def _stream(records: Iterable[Mapping[str, str]], label: str,
            state_hook: Optional[Callable[[str, int], None]]) -> Tuple[str, int, int]:
    if state_hook is not None:
        state_hook(label, 256)
    digest = hashlib.sha256(); total = 0; values = 0
    for record in records:
        encoded = _json_line(record)
        digest.update(encoded); total += len(encoded)
        values += sum(len(value.encode("utf-8")) for value in record.values())
    return digest.hexdigest(), total, values


def digest_relations(validated: PhaseBResult, derived: DerivedRelations,
                     state_hook: Optional[Callable[[str, int], None]] = None) -> DigestStreams:
    """Hash all 27 exact canonical streams in frozen table order."""
    obligations = sorted(validated.obligations,
                         key=lambda x: (x.lane_id, x.behavior_case_id, x.obligation_id))
    paths = sorted(derived.enforcement_paths,
                   key=lambda x: (x.path_id, x.obligation_id, x.case_id, x.judge_id,
                                  x.bundle_id, x.gate_id))
    entities: Tuple[Tuple[str, Iterable[Mapping[str, str]]], ...] = (
        ("lane", ({"lane_id": x.lane_id} for x in sorted(validated.lanes, key=lambda y: y.lane_id))),
        ("behavior_case", ({"lane_id": lane.lane_id, "behavior_case_id": case}
                           for lane in sorted(validated.lanes, key=lambda y: y.lane_id)
                           for case in sorted(lane.cases))),
        ("case_plan", ({"lane_id": lane.lane_id, "case_id": case}
                       for lane in sorted(validated.lanes, key=lambda y: y.lane_id)
                       for case in sorted(lane.case_ids))),
        ("obligation", ({"lane_id": x.lane_id, "behavior_case_id": x.behavior_case_id,
                         "obligation_id": x.obligation_id} for x in obligations)),
        ("judge", ({"judge_id": x} for x in sorted(validated.judges))),
        ("evidence_bundle", ({"lane_id": lane, "bundle_id": bundle}
                             for lane, bundle in sorted(validated.bundles))),
        ("evidence_source", ({"evidence_source_id": x} for x in sorted(validated.evidence_sources))),
        ("lane_context_contract", ({"artifact_type": x} for x in sorted({a for _, a in validated.lane_contexts}))),
        ("post_gate_output_contract", ({"artifact_type": x} for x in sorted({a for _, a in validated.lane_outputs}))),
        ("gate", ({"lane_id": lane, "gate_id": gate} for lane, gate in sorted(validated.gates))),
        ("judge_negative_template", ({"template_id": template} for _, template in sorted(validated.judge_templates, key=lambda x: x[1]))),
        ("aggregate_negative_template", ({"template_id": template} for _, template in sorted(validated.aggregate_templates, key=lambda x: x[1]))),
        ("plumbing_negative_template", ({"template_id": template} for _, template in sorted(validated.plumbing_templates, key=lambda x: x[1]))),
        ("enforcement_path", ({"path_id": p.path_id, "obligation_id": p.obligation_id,
                               "case_id": p.case_id, "judge_id": p.judge_id,
                               "bundle_id": p.bundle_id, "gate_id": p.gate_id} for p in paths)),
    )
    relations: Tuple[Tuple[str, Iterable[Mapping[str, str]]], ...] = (
        ("behavior_obligation", ({"lane_id": x.lane_id, "behavior_case_id": x.behavior_case_id,
                                  "obligation_id": x.obligation_id} for x in obligations)),
        ("obligation_judge", ({"obligation_id": o, "judge_id": j}
                              for o, j in sorted(validated.obligation_judges))),
        ("lane_bundle", ({"lane_id": lane, "bundle_id": bundle}
                         for lane, bundle in sorted(validated.bundles))),
        ("obligation_gate", ({"lane_id": p.lane_id, "obligation_id": p.obligation_id,
                              "gate_id": p.gate_id} for p in sorted(validated.pairs,
                              key=lambda x: (x.lane_id, x.obligation_id, x.gate_id)))),
        ("bundle_evidence", ({"bundle_id": b, "evidence_source_id": e}
                             for b, e in sorted(validated.bundle_evidence))),
        ("lane_context", ({"lane_id": lane, "artifact_type": artifact}
                          for lane, artifact in sorted(validated.lane_contexts))),
        ("lane_output", ({"lane_id": lane, "artifact_type": artifact}
                         for lane, artifact in sorted(validated.lane_outputs))),
        ("path_case", ({"path_id": h, "case_id": c} for h, c in sorted(derived.path_case))),
        ("path_gate", ({"path_id": h, "gate_id": g} for h, g in sorted(derived.path_gate))),
        ("path_evidence", ({"path_id": h, "evidence_source_id": e}
                           for h, e in sorted(derived.path_evidence))),
        ("path_judge_negative", ({"path_id": h, "template_id": n}
                                 for h, n in sorted(derived.path_judge_negative))),
        ("gate_aggregate_negative", ({"gate_id": g, "template_id": n}
                                     for g, n in sorted(derived.gate_aggregate_negative))),
        ("gate_plumbing_negative", ({"gate_id": g, "template_id": n}
                                    for g, n in sorted(derived.gate_plumbing_negative))),
    )
    entity_digests: Dict[str, str] = {}; relation_digests: Dict[str, str] = {}
    total = 0; value_total = 0
    for destination, streams in ((entity_digests, entities), (relation_digests, relations)):
        for name, records in streams:
            digest, byte_count, value_count = _stream(records, name, state_hook)
            destination[name + "_sha256"] = digest
            total += byte_count; value_total += value_count
    expected_total = 35158 + 177 * validated.h + 47 * validated.m + 39 * validated.r_he + value_total
    if total != expected_total:
        raise CompilerInternalError("U_digest formula diverged from canonical streams")
    return DigestStreams(entity_digests, relation_digests, total, value_total)


_KEY_ARITIES = {
    "case": 1, "lane-context": 2, "evidence": 2, "bundle": 1, "judge": 1,
    "gate-input": 1, "gate-result": 1, "judge-negative": 2,
    "gate-negative": 2, "plumbing-negative": 2, "lane-output": 2,
    "compile-attempt": 1, "compile-output": 1,
}
_LITERAL_KEYS = frozenset(("compile-history", "run-environment", "attempt-chronology",
                           "run-manifest", "report", "run-seal"))
_H_FAMILIES = frozenset(("evidence", "bundle", "judge", "judge-negative"))


def validate_key(key: str) -> Tuple[str, Tuple[str, ...]]:
    """Parse and canonicalize one frozen compile/run key."""
    if key in _LITERAL_KEYS:
        return key, ()
    parts = key.split("/")
    family = parts[0]
    if family not in _KEY_ARITIES or len(parts) - 1 != _KEY_ARITIES[family]:
        raise ValueError("unknown key family or wrong arity")
    components = parts[1:]
    if family in ("compile-attempt", "compile-output"):
        if not components[0].isdigit() or components[0].startswith("0"):
            raise ValueError("attempt is not canonical decimal")
        return family, (components[0],)
    decoded: List[str] = []
    for index, component in enumerate(components):
        if family in _H_FAMILIES and index == 0:
            decoded.extend(decode_enforcement_path(component))
        else:
            decoded.append(_decode_identifier(component))
    return family, tuple(decoded)


def enumerate_expected_keys(validated: PhaseBResult, derived: DerivedRelations,
                            t_max: int) -> ExpectedKeys:
    """Enumerate, collision-check, sort, and hash the complete K_run key set."""
    if isinstance(t_max, bool) or not isinstance(t_max, int) or t_max < 1:
        raise ValueError("t_max must be positive")
    execution: List[str] = []
    for lane in validated.lanes:
        execution.extend("case/" + pct_encode(case) for case in lane.case_ids)
    execution.extend("lane-context/%s/%s" % (pct_encode(lane), pct_encode(kind))
                     for lane, kind in validated.lane_contexts)
    execution.extend("evidence/%s/%s" % (h, pct_encode(source))
                     for h, source in derived.path_evidence)
    execution.extend("bundle/" + p.path_id for p in derived.enforcement_paths)
    execution.extend("judge/" + p.path_id for p in derived.enforcement_paths)
    execution.extend("gate-input/" + pct_encode(gate) for _lane, gate in validated.gates)
    execution.extend("gate-result/" + pct_encode(gate) for _lane, gate in validated.gates)
    execution.extend("judge-negative/%s/%s" % (h, pct_encode(template))
                     for h, template in derived.path_judge_negative)
    execution.extend("gate-negative/%s/%s" % (pct_encode(gate), pct_encode(template))
                     for gate, template in derived.gate_aggregate_negative)
    execution.extend("plumbing-negative/%s/%s" % (pct_encode(gate), pct_encode(template))
                     for gate, template in derived.gate_plumbing_negative)
    execution.extend("lane-output/%s/%s" % (pct_encode(lane), pct_encode(kind))
                     for lane, kind in validated.lane_outputs)
    compile_keys = ["compile-history"]
    compile_keys.extend("compile-attempt/%d" % value for value in range(1, t_max + 1))
    compile_keys.extend("compile-output/%d" % value for value in range(1, t_max + 1))
    run_global = ["run-environment", "attempt-chronology", "run-manifest", "report", "run-seal"]
    names = sorted(compile_keys + execution + run_global, key=lambda x: x.encode("ascii"))
    if len(names) != len(set(names)):
        _fail("traceability_relation_mismatch", "/traceability_ledger/cardinalities/key_count_exec")
    if len(execution) != validated.k_exec:
        _fail("traceability_relation_mismatch", "/traceability_ledger/cardinalities/key_count_exec")
    for name in names:
        validate_key(name)
    preimage = sum(len(name.encode("ascii")) + 1 for name in names)
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("ascii") + b"\n")
    return ExpectedKeys(tuple(names), digest.hexdigest(), len(names), len(compile_keys),
                        len(execution), len(run_global), preimage,
                        preimage + 2 * len(names) + 1)


def compile_relations(catalog: Any, expectation: Any, ledger: Any, t_max: int,
                      limits: Optional[CompileLimits] = None,
                      allocation_hook: Optional[Callable[[str, int], None]] = None,
                      digest_state_hook: Optional[Callable[[str, int], None]] = None
                      ) -> CompiledRelations:
    """Execute T6's Phase B--E relation work as one coherent operation."""
    validated = validate_relations(catalog, expectation, ledger, limits)
    derived = derive_relations(validated, allocation_hook)
    digests = digest_relations(validated, derived, digest_state_hook)
    keys = enumerate_expected_keys(validated, derived, t_max)
    return CompiledRelations(validated, derived, digests, keys)
