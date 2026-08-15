from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .core import ContractError, IDENTIFIER_RE
from .expectation import ExpectationLane, Gate, ImplementationExpectation


LANE_SUITE_SCHEMA = "turnvector.benchmark.lane-suite.v1"
CASE_SCHEMA = "turnvector.benchmark.case-schema.v1"
SUBJECT_MANIFEST_SCHEMA = "turnvector.benchmark.subject-manifest.v1"
CERTIFICATION_RECORD_SCHEMA = "turnvector.benchmark.certification-record.v1"
SUBJECT_PROTOCOL = "turnvector.benchmark.subject.v1"

ADAPTER_CATEGORIES = {"core", "native", "system"}
SUBJECT_KINDS = {"implementation", "fixture"}
REDUCERS = {"sum", "max", "min", "p50", "p95", "p99", "rate", "any"}
EXECUTION_BOUNDARIES = {
    "core_adapter",
    "native_adapter",
    "direct_data_plane",
    "benchmark_orchestrated",
}


def _object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _strict_keys(
    value: Mapping[str, Any], required: Iterable[str], optional: Iterable[str], where: str
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
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


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ContractError(f"{where} must be finite")
    return parsed


def _strings(value: Any, where: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    parsed = tuple(
        _identifier(item, f"{where}[{index}]")
        for index, item in enumerate(_array(value, where))
    )
    if not parsed and not allow_empty:
        raise ContractError(f"{where} must not be empty")
    if len(parsed) != len(set(parsed)):
        raise ContractError(f"{where} must not contain duplicates")
    return parsed


def _read_json(path: Path, kind: str) -> Dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read {kind} {path}: {error}") from error


@dataclass(frozen=True)
class MetricRecipe:
    metric: str
    source: str
    reducer: str


@dataclass(frozen=True)
class LaneRequirements:
    execution_boundary: str
    real_subject_required: bool
    external_inputs: Tuple[str, ...]
    diagnostic_only_cases: Tuple[str, ...]


@dataclass(frozen=True)
class LaneSuite:
    suite_id: str
    lane_id: str
    protocol: str
    runner: str
    adapter_category: str
    matrix_ids: Tuple[str, ...]
    behavior_case_ids: Tuple[str, ...]
    gate_ids: Tuple[str, ...]
    case_schema_path: Path
    operations: Tuple[str, ...]
    metrics: Tuple[MetricRecipe, ...]
    requirements: LaneRequirements
    source_path: Path

    def metric_recipe(self, metric: str) -> MetricRecipe:
        for recipe in self.metrics:
            if recipe.metric == metric:
                return recipe
        raise ContractError(f"suite {self.suite_id!r} has no recipe for metric {metric!r}")


@dataclass(frozen=True)
class PlannedCase:
    case_id: str
    lane_id: str
    matrix_id: str
    ordinal: int
    parameters: Mapping[str, Any]
    behavior_case_ids: Tuple[str, ...]
    operations: Tuple[str, ...]
    diagnostic_only: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "lane_id": self.lane_id,
            "matrix_id": self.matrix_id,
            "ordinal": self.ordinal,
            "parameters": dict(self.parameters),
            "behavior_case_ids": list(self.behavior_case_ids),
            "operations": list(self.operations),
            "diagnostic_only": self.diagnostic_only,
        }


@dataclass(frozen=True)
class CasePlan:
    lane_id: str
    suite_id: str
    cases: Tuple[PlannedCase, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "turnvector.benchmark.case-plan.v1",
            "lane_id": self.lane_id,
            "suite_id": self.suite_id,
            "case_count": len(self.cases),
            "cases": [case.as_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class SubjectAdapter:
    adapter_id: str
    category: str
    command: Tuple[str, ...]
    cwd: Path
    lanes: Tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class SubjectManifest:
    manifest_id: str
    subject_kind: str
    adapters: Tuple[SubjectAdapter, ...]
    source_path: Path

    def adapter_for_lane(self, lane_id: str) -> Optional[SubjectAdapter]:
        found = [adapter for adapter in self.adapters if lane_id in adapter.lanes]
        if len(found) > 1:
            raise ContractError(f"subject manifest maps lane {lane_id!r} more than once")
        return found[0] if found else None


@dataclass(frozen=True)
class CertificationRecord:
    record_id: str
    subject_build_identity: str
    protocol_version: str
    issued_at: datetime
    expires_at: datetime
    environment_identity: Mapping[str, Any]
    matrix_applicability: Mapping[str, Tuple[Any, ...]]
    thresholds: Mapping[str, Mapping[str, Any]]
    source_path: Path

    def threshold(self, lane_id: str, gate: Gate) -> Any:
        lane_thresholds = self.thresholds.get(lane_id)
        if lane_thresholds is None or gate.metric not in lane_thresholds:
            raise ContractError(
                f"certification record {self.record_id!r} has no pre-run threshold for "
                f"{lane_id}.{gate.metric}"
            )
        return lane_thresholds[gate.metric]

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        observed = now or datetime.now(timezone.utc)
        return observed >= self.expires_at

    def is_not_yet_valid(self, now: Optional[datetime] = None) -> bool:
        observed = now or datetime.now(timezone.utc)
        return observed < self.issued_at


def _validate_case_schema(path: Path, lane: ExpectationLane) -> None:
    obj = _read_json(path, "case schema")
    _strict_keys(obj, ["schema_version", "lane_id", "matrices"], [], str(path))
    if obj["schema_version"] != CASE_SCHEMA:
        raise ContractError(f"{path}.schema_version must be {CASE_SCHEMA!r}")
    if obj["lane_id"] != lane.lane_id:
        raise ContractError(f"{path}.lane_id does not match {lane.lane_id!r}")
    matrices_obj = _object(obj["matrices"], f"{path}.matrices")
    expected_ids = {matrix.matrix_id for matrix in lane.matrices}
    if set(matrices_obj) != expected_ids:
        raise ContractError(
            f"{path}.matrices must exactly match expectation matrices {sorted(expected_ids)!r}"
        )
    for matrix in lane.matrices:
        where = f"{path}.matrices.{matrix.matrix_id}"
        matrix_obj = _object(matrices_obj[matrix.matrix_id], where)
        _strict_keys(matrix_obj, ["required", "properties", "additional_properties"], [], where)
        required = _strings(matrix_obj["required"], f"{where}.required")
        expected_dimensions = tuple(item.dimension_id for item in matrix.dimensions)
        if required != expected_dimensions:
            raise ContractError(
                f"{where}.required must preserve expectation dimension order "
                f"{expected_dimensions!r}"
            )
        if _boolean(matrix_obj["additional_properties"], f"{where}.additional_properties"):
            raise ContractError(f"{where}.additional_properties must be false")
        properties = _object(matrix_obj["properties"], f"{where}.properties")
        if set(properties) != set(expected_dimensions):
            raise ContractError(f"{where}.properties must exactly match required dimensions")
        for dimension in matrix.dimensions:
            property_where = f"{where}.properties.{dimension.dimension_id}"
            property_obj = _object(properties[dimension.dimension_id], property_where)
            _strict_keys(property_obj, ["enum"], [], property_where)
            observed = tuple(_array(property_obj["enum"], f"{property_where}.enum"))
            if observed != dimension.values:
                raise ContractError(
                    f"{property_where}.enum must exactly match expectation values"
                )


def load_lane_suite(path: Path, lane: ExpectationLane) -> LaneSuite:
    obj = _read_json(path, "lane suite")
    _strict_keys(
        obj,
        [
            "schema_version",
            "id",
            "lane_id",
            "protocol",
            "runner",
            "adapter_category",
            "matrix_ids",
            "behavior_case_ids",
            "gate_ids",
            "case_schema",
            "operations",
            "metrics",
            "requirements",
        ],
        [],
        str(path),
    )
    if obj["schema_version"] != LANE_SUITE_SCHEMA:
        raise ContractError(f"{path}.schema_version must be {LANE_SUITE_SCHEMA!r}")
    lane_id = _identifier(obj["lane_id"], f"{path}.lane_id")
    if lane_id != lane.lane_id:
        raise ContractError(f"{path}.lane_id must equal {lane.lane_id!r}")
    matrix_ids = _strings(obj["matrix_ids"], f"{path}.matrix_ids")
    behavior_case_ids = _strings(obj["behavior_case_ids"], f"{path}.behavior_case_ids")
    gate_ids = _strings(obj["gate_ids"], f"{path}.gate_ids")
    expected_matrix_ids = tuple(item.matrix_id for item in lane.matrices)
    expected_behavior_ids = tuple(item.case_id for item in lane.cases)
    expected_gate_ids = tuple(item.gate_id for item in lane.gates)
    for observed, expected, label in (
        (matrix_ids, expected_matrix_ids, "matrix_ids"),
        (behavior_case_ids, expected_behavior_ids, "behavior_case_ids"),
        (gate_ids, expected_gate_ids, "gate_ids"),
    ):
        if observed != expected:
            raise ContractError(
                f"{path}.{label} must exactly match expectation order {expected!r}"
            )
    protocol = _identifier(obj["protocol"], f"{path}.protocol")
    runner = _identifier(obj["runner"], f"{path}.runner")
    if protocol != lane.harness.protocol or runner != lane.harness.runner:
        raise ContractError(f"{path} protocol/runner does not match expectation harness")
    category = _identifier(obj["adapter_category"], f"{path}.adapter_category")
    if category not in ADAPTER_CATEGORIES:
        raise ContractError(f"{path}.adapter_category must be one of {sorted(ADAPTER_CATEGORIES)}")
    recipes: List[MetricRecipe] = []
    for index, value in enumerate(_array(obj["metrics"], f"{path}.metrics")):
        where = f"{path}.metrics[{index}]"
        metric_obj = _object(value, where)
        _strict_keys(metric_obj, ["metric", "source", "reducer"], [], where)
        reducer = _identifier(metric_obj["reducer"], f"{where}.reducer")
        if reducer not in REDUCERS:
            raise ContractError(f"{where}.reducer must be one of {sorted(REDUCERS)}")
        recipes.append(
            MetricRecipe(
                metric=_identifier(metric_obj["metric"], f"{where}.metric"),
                source=_identifier(metric_obj["source"], f"{where}.source"),
                reducer=reducer,
            )
        )
    expected_metrics = tuple(gate.metric for gate in lane.gates)
    if tuple(recipe.metric for recipe in recipes) != expected_metrics:
        raise ContractError(f"{path}.metrics must define one ordered recipe per gate")
    if len({recipe.source for recipe in recipes}) != len(recipes):
        raise ContractError(f"{path}.metrics sources must be unique")
    requirements_obj = _object(obj["requirements"], f"{path}.requirements")
    _strict_keys(
        requirements_obj,
        [
            "execution_boundary",
            "real_subject_required",
            "external_inputs",
            "diagnostic_only_cases",
        ],
        [],
        f"{path}.requirements",
    )
    boundary = _identifier(
        requirements_obj["execution_boundary"],
        f"{path}.requirements.execution_boundary",
    )
    if boundary not in EXECUTION_BOUNDARIES:
        raise ContractError(
            f"{path}.requirements.execution_boundary must be one of "
            f"{sorted(EXECUTION_BOUNDARIES)}"
        )
    case_schema_path = (path.parent / _string(obj["case_schema"], f"{path}.case_schema")).resolve()
    if not case_schema_path.is_file():
        raise ContractError(f"{path}.case_schema does not exist: {case_schema_path}")
    _validate_case_schema(case_schema_path, lane)
    return LaneSuite(
        suite_id=_identifier(obj["id"], f"{path}.id"),
        lane_id=lane_id,
        protocol=protocol,
        runner=runner,
        adapter_category=category,
        matrix_ids=matrix_ids,
        behavior_case_ids=behavior_case_ids,
        gate_ids=gate_ids,
        case_schema_path=case_schema_path,
        operations=_strings(obj["operations"], f"{path}.operations"),
        metrics=tuple(recipes),
        requirements=LaneRequirements(
            execution_boundary=boundary,
            real_subject_required=_boolean(
                requirements_obj["real_subject_required"],
                f"{path}.requirements.real_subject_required",
            ),
            external_inputs=_strings(
                requirements_obj["external_inputs"],
                f"{path}.requirements.external_inputs",
                allow_empty=True,
            ),
            diagnostic_only_cases=_strings(
                requirements_obj["diagnostic_only_cases"],
                f"{path}.requirements.diagnostic_only_cases",
                allow_empty=True,
            ),
        ),
        source_path=path.resolve(),
    )


def load_all_lane_suites(expectation: ImplementationExpectation) -> Dict[str, LaneSuite]:
    suites: Dict[str, LaneSuite] = {}
    for lane in expectation.lanes:
        if lane.harness.entrypoint is None:
            raise ContractError(f"lane {lane.lane_id!r} has no lane suite")
        path = (expectation.source_path.parent / lane.harness.entrypoint).resolve()
        suites[lane.lane_id] = load_lane_suite(path, lane)
    return suites


def expand_case_plan(lane: ExpectationLane, suite: LaneSuite) -> CasePlan:
    cases: List[PlannedCase] = []
    ordinal = 0
    diagnostic = set(suite.requirements.diagnostic_only_cases)
    for matrix in lane.matrices:
        names = [dimension.dimension_id for dimension in matrix.dimensions]
        values = [dimension.values for dimension in matrix.dimensions]
        for combination in product(*values):
            ordinal += 1
            parameters = dict(zip(names, combination))
            case_id = f"{lane.lane_id}.{matrix.matrix_id}.{ordinal:04d}"
            cases.append(
                PlannedCase(
                    case_id=case_id,
                    lane_id=lane.lane_id,
                    matrix_id=matrix.matrix_id,
                    ordinal=ordinal,
                    parameters=parameters,
                    behavior_case_ids=suite.behavior_case_ids,
                    operations=suite.operations,
                    diagnostic_only=case_id in diagnostic,
                )
            )
    if len(cases) != lane.expanded_matrix_case_count:
        raise ContractError(f"case plan for {lane.lane_id!r} lost expectation combinations")
    return CasePlan(lane_id=lane.lane_id, suite_id=suite.suite_id, cases=tuple(cases))


def load_subject_manifest(path: Path, expectation: ImplementationExpectation) -> SubjectManifest:
    obj = _read_json(path, "subject manifest")
    _strict_keys(obj, ["schema_version", "id", "subject_kind", "adapters"], [], str(path))
    if obj["schema_version"] != SUBJECT_MANIFEST_SCHEMA:
        raise ContractError(f"{path}.schema_version must be {SUBJECT_MANIFEST_SCHEMA!r}")
    subject_kind = _identifier(obj["subject_kind"], f"{path}.subject_kind")
    if subject_kind not in SUBJECT_KINDS:
        raise ContractError(f"{path}.subject_kind must be one of {sorted(SUBJECT_KINDS)}")
    known_lanes = {lane.lane_id for lane in expectation.lanes}
    adapters: List[SubjectAdapter] = []
    mapped: set[str] = set()
    for index, value in enumerate(_array(obj["adapters"], f"{path}.adapters")):
        where = f"{path}.adapters[{index}]"
        adapter_obj = _object(value, where)
        _strict_keys(
            adapter_obj,
            ["id", "category", "command", "cwd", "lanes", "timeout_seconds"],
            [],
            where,
        )
        category = _identifier(adapter_obj["category"], f"{where}.category")
        if category not in ADAPTER_CATEGORIES:
            raise ContractError(f"{where}.category must be one of {sorted(ADAPTER_CATEGORIES)}")
        command = tuple(
            _string(item, f"{where}.command[{command_index}]")
            for command_index, item in enumerate(_array(adapter_obj["command"], f"{where}.command"))
        )
        if not command:
            raise ContractError(f"{where}.command must not be empty")
        lanes = _strings(adapter_obj["lanes"], f"{where}.lanes")
        unknown = sorted(set(lanes) - known_lanes)
        duplicates = sorted(set(lanes) & mapped)
        if unknown:
            raise ContractError(f"{where}.lanes contains unknown lanes: {unknown!r}")
        if duplicates:
            raise ContractError(f"{where}.lanes maps lanes more than once: {duplicates!r}")
        mapped.update(lanes)
        timeout_seconds = _number(adapter_obj["timeout_seconds"], f"{where}.timeout_seconds")
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ContractError(f"{where}.timeout_seconds must be in (0, 3600]")
        cwd = (path.parent / _string(adapter_obj["cwd"], f"{where}.cwd")).resolve()
        if not cwd.is_dir():
            raise ContractError(f"{where}.cwd is not a directory: {cwd}")
        adapters.append(
            SubjectAdapter(
                adapter_id=_identifier(adapter_obj["id"], f"{where}.id"),
                category=category,
                command=command,
                cwd=cwd,
                lanes=lanes,
                timeout_seconds=timeout_seconds,
            )
        )
    adapter_ids = [adapter.adapter_id for adapter in adapters]
    if len(adapter_ids) != len(set(adapter_ids)):
        raise ContractError(f"{path}.adapters contains duplicate IDs")
    return SubjectManifest(
        manifest_id=_identifier(obj["id"], f"{path}.id"),
        subject_kind=subject_kind,
        adapters=tuple(adapters),
        source_path=path.resolve(),
    )


def _parse_timestamp(value: Any, where: str) -> datetime:
    text = _string(value, where)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{where} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{where} must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_certification_record(path: Path) -> CertificationRecord:
    obj = _read_json(path, "certification record")
    _strict_keys(
        obj,
        [
            "schema_version",
            "id",
            "subject_build_identity",
            "protocol_version",
            "issued_at",
            "expires_at",
            "environment_identity",
            "matrix_applicability",
            "thresholds",
        ],
        [],
        str(path),
    )
    if obj["schema_version"] != CERTIFICATION_RECORD_SCHEMA:
        raise ContractError(f"{path}.schema_version must be {CERTIFICATION_RECORD_SCHEMA!r}")
    issued_at = _parse_timestamp(obj["issued_at"], f"{path}.issued_at")
    expires_at = _parse_timestamp(obj["expires_at"], f"{path}.expires_at")
    if expires_at <= issued_at:
        raise ContractError(f"{path}.expires_at must be after issued_at")
    environment = _object(obj["environment_identity"], f"{path}.environment_identity")
    if not environment:
        raise ContractError(f"{path}.environment_identity must not be empty")
    matrix_obj = _object(obj["matrix_applicability"], f"{path}.matrix_applicability")
    matrix_applicability: Dict[str, Tuple[Any, ...]] = {}
    for key, values in matrix_obj.items():
        _identifier(key, f"{path}.matrix_applicability key")
        parsed_values = tuple(_array(values, f"{path}.matrix_applicability.{key}"))
        if not parsed_values:
            raise ContractError(f"{path}.matrix_applicability.{key} must not be empty")
        serialized = [json.dumps(item, sort_keys=True) for item in parsed_values]
        if len(serialized) != len(set(serialized)):
            raise ContractError(f"{path}.matrix_applicability.{key} has duplicate values")
        matrix_applicability[key] = parsed_values
    thresholds_obj = _object(obj["thresholds"], f"{path}.thresholds")
    thresholds: Dict[str, Mapping[str, Any]] = {}
    for lane_id, raw_values in thresholds_obj.items():
        _identifier(lane_id, f"{path}.thresholds lane")
        values = _object(raw_values, f"{path}.thresholds.{lane_id}")
        parsed: Dict[str, Any] = {}
        for metric, value in values.items():
            _identifier(metric, f"{path}.thresholds.{lane_id} metric")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError(
                    f"{path}.thresholds.{lane_id}.{metric} must be a pre-run number"
                )
            parsed[metric] = value
        thresholds[lane_id] = parsed
    return CertificationRecord(
        record_id=_identifier(obj["id"], f"{path}.id"),
        subject_build_identity=_string(
            obj["subject_build_identity"], f"{path}.subject_build_identity"
        ),
        protocol_version=_identifier(obj["protocol_version"], f"{path}.protocol_version"),
        issued_at=issued_at,
        expires_at=expires_at,
        environment_identity=dict(environment),
        matrix_applicability=matrix_applicability,
        thresholds=thresholds,
        source_path=path.resolve(),
    )


def resolve_gate_threshold(
    lane_id: str, gate: Gate, record: Optional[CertificationRecord]
) -> Any:
    if gate.threshold_source == "certification_record":
        if record is None:
            raise ContractError(
                f"lane {lane_id!r} requires a candidate certification record before execution"
            )
        if record.is_not_yet_valid():
            raise ContractError(
                f"certification record {record.record_id!r} is not yet valid"
            )
        if record.is_expired():
            raise ContractError(f"certification record {record.record_id!r} is expired")
        return record.threshold(lane_id, gate)
    return gate.expected


def validate_certification_contract(
    record: CertificationRecord, expectation: ImplementationExpectation
) -> None:
    if record.protocol_version != SUBJECT_PROTOCOL:
        raise ContractError(
            "certification record protocol does not match SubjectAdapter v1"
        )
    if record.is_not_yet_valid():
        raise ContractError(
            f"certification record {record.record_id!r} is not yet valid"
        )
    if record.is_expired():
        raise ContractError(f"certification record {record.record_id!r} is expired")

    dimension_domains: Dict[str, Dict[str, Any]] = {}
    for lane in expectation.lanes:
        for matrix in lane.matrices:
            for dimension in matrix.dimensions:
                domain = dimension_domains.setdefault(dimension.dimension_id, {})
                for value in dimension.values:
                    domain[json.dumps(value, sort_keys=True)] = value
    expected_dimensions = set(dimension_domains)
    observed_dimensions = set(record.matrix_applicability)
    if observed_dimensions != expected_dimensions:
        raise ContractError(
            "certification record matrix applicability must cover every qualification "
            f"dimension exactly: missing={sorted(expected_dimensions - observed_dimensions)!r}, "
            f"unknown={sorted(observed_dimensions - expected_dimensions)!r}"
        )
    for dimension, expected_by_identity in dimension_domains.items():
        observed_by_identity = {
            json.dumps(value, sort_keys=True): value
            for value in record.matrix_applicability[dimension]
        }
        if set(observed_by_identity) != set(expected_by_identity):
            raise ContractError(
                f"certification record applicability for {dimension!r} must exactly cover "
                "the qualification domain"
            )

    expected_thresholds = {
        lane.lane_id: {
            gate.metric
            for gate in lane.gates
            if gate.threshold_source == "certification_record"
        }
        for lane in expectation.lanes
    }
    expected_thresholds = {
        lane_id: metrics for lane_id, metrics in expected_thresholds.items() if metrics
    }
    if set(record.thresholds) != set(expected_thresholds):
        raise ContractError(
            "certification record threshold lanes must exactly match the expectation"
        )
    for lane_id, metrics in expected_thresholds.items():
        if set(record.thresholds[lane_id]) != metrics:
            raise ContractError(
                f"certification record thresholds for {lane_id!r} must exactly match "
                "certification-sourced gates"
            )


def validate_certification_identity(
    record: CertificationRecord,
    *,
    subject_build_identity: str,
    environment_identity: Mapping[str, Any],
    plan: CasePlan,
) -> None:
    if record.subject_build_identity != subject_build_identity:
        raise ContractError(
            f"certification record build {record.subject_build_identity!r} does not apply to "
            f"subject build {subject_build_identity!r}"
        )
    if dict(environment_identity) != dict(record.environment_identity):
        differing = sorted(
            key
            for key in set(environment_identity) | set(record.environment_identity)
            if environment_identity.get(key) != record.environment_identity.get(key)
        )
        raise ContractError(
            "certification record environment identity does not apply exactly; "
            f"differing keys={differing!r}"
        )
    for case in plan.cases:
        for key, observed in case.parameters.items():
            permitted = record.matrix_applicability.get(key)
            if permitted is None:
                raise ContractError(
                    f"certification record has no applicability domain for {key!r}"
                )
            if observed not in permitted:
                raise ContractError(
                    f"certification record does not apply to {case.case_id}: "
                    f"{key}={observed!r}"
                )
