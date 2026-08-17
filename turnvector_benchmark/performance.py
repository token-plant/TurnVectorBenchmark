from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .core import ContractError, IDENTIFIER_RE
from .evidence import sha256_file, validate_subject_artifact


CONTRACT_SCHEMA = "turnvector.benchmark.performance-contract.v1"
EVIDENCE_SCHEMA = "turnvector.benchmark.performance-evidence.v1"
CERTIFICATION_SCHEMA = "turnvector.benchmark.performance-certification.v1"
REPORT_SCHEMA = "turnvector.benchmark.performance-validation-report.v1"

ACTIVATIONS = {"core", "capability_conditioned"}
CLAIM_TYPES = {"absolute", "paired_delta", "correctness"}
PROCESS_ISOLATION = {"fresh_per_repetition", "fresh_per_cell", "session_reuse"}
ORDER_POLICIES = {"balanced", "alternating", "fixed"}
CACHE_POLICIES = {"disabled", "isolated", "scenario_defined"}
SAMPLING_POLICIES = {"greedy", "sampled", "none"}
REDUCERS = {"median", "p95", "p99", "min", "max", "sum"}
GATE_OPERATORS = {"eq", "gte", "lte"}
THRESHOLD_SOURCES = {"benchmark_contract", "certification_record"}
GATE_DECISIONS = {"evidence", "promotion"}

ENVIRONMENT_IDENTITY_FIELDS = (
    "device_class",
    "memory_bytes",
    "os_build",
    "resolved_runtime_sha256",
    "subject_binary_sha256",
    "model_manifest_sha256",
    "prompt_manifest_sha256",
    "certification_record_sha256",
)
HOST_CONDITION_FIELDS = (
    "power_source",
    "thermal_state_start",
    "thermal_state_end",
    "load_average_1m_start",
    "load_average_1m_end",
    "top_process_cpu_percent_start",
    "top_process_cpu_percent_end",
    "host_admission_passed",
)
BENCHMARK_CUSTODY_FIELDS = (
    "host_conditions",
    "request_timestamps",
    "raw_trials",
)
COMMON_REQUIRED_ARTIFACTS = (
    "run_manifest",
    "environment",
    "raw_trials",
    "host_samples",
    "model_manifest",
    "prompt_manifest",
    "certification_record",
)


def _object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _strict_keys(
    value: Mapping[str, Any], required: Sequence[str], optional: Sequence[str], where: str
) -> None:
    required_keys = set(required)
    allowed = required_keys | set(optional)
    missing = sorted(required_keys - set(value))
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


def _number(value: Any, where: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ContractError(f"{where} must be finite")
    if nonnegative and parsed < 0:
        raise ContractError(f"{where} must be non-negative")
    return parsed


def _integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{where} must be an integer greater than or equal to {minimum}")
    return value


def _scalar(value: Any, where: str) -> Any:
    if isinstance(value, bool) or isinstance(value, str) or isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ContractError(f"{where} must be a finite scalar")


def _digest(value: Any, where: str, *, length: int = 64) -> str:
    text = _string(value, where)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ContractError(f"{where} must be a lowercase {length}-character hex digest")
    return text


def _strings(
    value: Any, where: str, *, identifiers: bool = False, allow_empty: bool = False
) -> Tuple[str, ...]:
    parser = _identifier if identifiers else _string
    parsed = tuple(
        parser(item, f"{where}[{index}]")
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


def _timestamp(value: Any, where: str) -> datetime:
    text = _string(value, where)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{where} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{where} must include a timezone")
    return parsed


@dataclass(frozen=True)
class PerformanceDimension:
    dimension_id: str
    values: Tuple[Any, ...]


@dataclass(frozen=True)
class PerformanceMatrix:
    matrix_id: str
    dimensions: Tuple[PerformanceDimension, ...]

    @property
    def case_count(self) -> int:
        count = 1
        for dimension in self.dimensions:
            count *= len(dimension.values)
        return count


@dataclass(frozen=True)
class PerformanceProtocol:
    warmup_repetitions: int
    measured_repetitions: int
    cooldown_seconds: float
    process_isolation: str
    order_policy: str
    cache_policy: str
    sampling_policy: str
    fixed_parameters: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "warmup_repetitions": self.warmup_repetitions,
            "measured_repetitions": self.measured_repetitions,
            "cooldown_seconds": self.cooldown_seconds,
            "process_isolation": self.process_isolation,
            "order_policy": self.order_policy,
            "cache_policy": self.cache_policy,
            "sampling_policy": self.sampling_policy,
            "fixed_parameters": dict(self.fixed_parameters),
            "thresholds_frozen_before_run": True,
        }


@dataclass(frozen=True)
class PerformanceMetric:
    metric_id: str
    unit: str
    reducer: str


@dataclass(frozen=True)
class PerformanceGate:
    gate_id: str
    metric: str
    operator: str
    threshold_source: str
    expected: Optional[Any]
    decision: str


@dataclass(frozen=True)
class PerformanceLane:
    lane_id: str
    description: str
    activation: str
    session_mode: str
    claim_types: Tuple[str, ...]
    matrices: Tuple[PerformanceMatrix, ...]
    protocol: PerformanceProtocol
    metrics: Tuple[PerformanceMetric, ...]
    gates: Tuple[PerformanceGate, ...]
    required_artifacts: Tuple[str, ...]
    claim_boundaries: Tuple[str, ...]

    @property
    def case_count(self) -> int:
        return sum(matrix.case_count for matrix in self.matrices)


@dataclass(frozen=True)
class PlannedPerformanceCase:
    case_id: str
    lane_id: str
    matrix_id: str
    ordinal: int
    parameters: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "lane_id": self.lane_id,
            "matrix_id": self.matrix_id,
            "ordinal": self.ordinal,
            "parameters": dict(self.parameters),
        }


def _parse_matrix(value: Any, where: str) -> PerformanceMatrix:
    obj = _object(value, where)
    _strict_keys(obj, ["id", "dimensions"], [], where)
    dimensions: List[PerformanceDimension] = []
    for index, raw in enumerate(_array(obj["dimensions"], f"{where}.dimensions")):
        dimension_where = f"{where}.dimensions[{index}]"
        dimension_obj = _object(raw, dimension_where)
        _strict_keys(dimension_obj, ["id", "values"], [], dimension_where)
        values = tuple(
            _scalar(item, f"{dimension_where}.values[{value_index}]")
            for value_index, item in enumerate(
                _array(dimension_obj["values"], f"{dimension_where}.values")
            )
        )
        if not values:
            raise ContractError(f"{dimension_where}.values must not be empty")
        serialized = [json.dumps(item, sort_keys=True) for item in values]
        if len(serialized) != len(set(serialized)):
            raise ContractError(f"{dimension_where}.values must not contain duplicates")
        dimensions.append(
            PerformanceDimension(
                dimension_id=_identifier(
                    dimension_obj["id"], f"{dimension_where}.id"
                ),
                values=values,
            )
        )
    dimension_ids = [item.dimension_id for item in dimensions]
    if not dimensions or len(dimension_ids) != len(set(dimension_ids)):
        raise ContractError(f"{where}.dimensions must be non-empty and have unique IDs")
    return PerformanceMatrix(
        matrix_id=_identifier(obj["id"], f"{where}.id"),
        dimensions=tuple(dimensions),
    )


def _parse_protocol(value: Any, where: str) -> PerformanceProtocol:
    obj = _object(value, where)
    keys = [
        "warmup_repetitions",
        "measured_repetitions",
        "cooldown_seconds",
        "process_isolation",
        "order_policy",
        "cache_policy",
        "sampling_policy",
        "fixed_parameters",
    ]
    _strict_keys(obj, keys, [], where)
    process_isolation = _identifier(obj["process_isolation"], f"{where}.process_isolation")
    order_policy = _identifier(obj["order_policy"], f"{where}.order_policy")
    cache_policy = _identifier(obj["cache_policy"], f"{where}.cache_policy")
    sampling_policy = _identifier(obj["sampling_policy"], f"{where}.sampling_policy")
    for observed, allowed, label in (
        (process_isolation, PROCESS_ISOLATION, "process_isolation"),
        (order_policy, ORDER_POLICIES, "order_policy"),
        (cache_policy, CACHE_POLICIES, "cache_policy"),
        (sampling_policy, SAMPLING_POLICIES, "sampling_policy"),
    ):
        if observed not in allowed:
            raise ContractError(f"{where}.{label} must be one of {sorted(allowed)}")
    fixed_obj = _object(obj["fixed_parameters"], f"{where}.fixed_parameters")
    fixed_parameters: Dict[str, Any] = {}
    for key, raw in fixed_obj.items():
        fixed_parameters[_identifier(key, f"{where}.fixed_parameters key")] = _scalar(
            raw, f"{where}.fixed_parameters.{key}"
        )
    return PerformanceProtocol(
        warmup_repetitions=_integer(
            obj["warmup_repetitions"], f"{where}.warmup_repetitions"
        ),
        measured_repetitions=_integer(
            obj["measured_repetitions"], f"{where}.measured_repetitions", minimum=1
        ),
        cooldown_seconds=_number(
            obj["cooldown_seconds"], f"{where}.cooldown_seconds", nonnegative=True
        ),
        process_isolation=process_isolation,
        order_policy=order_policy,
        cache_policy=cache_policy,
        sampling_policy=sampling_policy,
        fixed_parameters=fixed_parameters,
    )


def _parse_lane(value: Any, where: str) -> PerformanceLane:
    obj = _object(value, where)
    keys = [
        "id",
        "description",
        "activation",
        "session_mode",
        "claim_types",
        "matrices",
        "protocol",
        "metrics",
        "gates",
        "required_artifacts",
        "claim_boundaries",
    ]
    _strict_keys(obj, keys, [], where)
    activation = _identifier(obj["activation"], f"{where}.activation")
    if activation not in ACTIVATIONS:
        raise ContractError(f"{where}.activation must be one of {sorted(ACTIVATIONS)}")
    claim_types = _strings(obj["claim_types"], f"{where}.claim_types", identifiers=True)
    if not set(claim_types).issubset(CLAIM_TYPES):
        raise ContractError(f"{where}.claim_types contains an unsupported claim type")
    matrices = tuple(
        _parse_matrix(item, f"{where}.matrices[{index}]")
        for index, item in enumerate(_array(obj["matrices"], f"{where}.matrices"))
    )
    matrix_ids = [item.matrix_id for item in matrices]
    if not matrices or len(matrix_ids) != len(set(matrix_ids)):
        raise ContractError(f"{where}.matrices must be non-empty and have unique IDs")
    metrics: List[PerformanceMetric] = []
    for index, raw in enumerate(_array(obj["metrics"], f"{where}.metrics")):
        metric_where = f"{where}.metrics[{index}]"
        metric_obj = _object(raw, metric_where)
        _strict_keys(metric_obj, ["id", "unit", "reducer"], [], metric_where)
        reducer = _identifier(metric_obj["reducer"], f"{metric_where}.reducer")
        if reducer not in REDUCERS:
            raise ContractError(f"{metric_where}.reducer must be one of {sorted(REDUCERS)}")
        metrics.append(
            PerformanceMetric(
                metric_id=_identifier(metric_obj["id"], f"{metric_where}.id"),
                unit=_identifier(metric_obj["unit"], f"{metric_where}.unit"),
                reducer=reducer,
            )
        )
    metric_ids = [item.metric_id for item in metrics]
    if not metrics or len(metric_ids) != len(set(metric_ids)):
        raise ContractError(f"{where}.metrics must be non-empty and have unique IDs")
    gates: List[PerformanceGate] = []
    for index, raw in enumerate(_array(obj["gates"], f"{where}.gates")):
        gate_where = f"{where}.gates[{index}]"
        gate_obj = _object(raw, gate_where)
        _strict_keys(
            gate_obj,
            ["id", "metric", "operator", "threshold_source", "expected", "decision"],
            [],
            gate_where,
        )
        metric = _identifier(gate_obj["metric"], f"{gate_where}.metric")
        if metric not in metric_ids:
            raise ContractError(f"{gate_where}.metric is not defined by the lane")
        operator = _identifier(gate_obj["operator"], f"{gate_where}.operator")
        source = _identifier(
            gate_obj["threshold_source"], f"{gate_where}.threshold_source"
        )
        decision = _identifier(gate_obj["decision"], f"{gate_where}.decision")
        if operator not in GATE_OPERATORS:
            raise ContractError(f"{gate_where}.operator must be one of {sorted(GATE_OPERATORS)}")
        if source not in THRESHOLD_SOURCES:
            raise ContractError(
                f"{gate_where}.threshold_source must be one of {sorted(THRESHOLD_SOURCES)}"
            )
        if decision not in GATE_DECISIONS:
            raise ContractError(f"{gate_where}.decision must be one of {sorted(GATE_DECISIONS)}")
        expected = gate_obj["expected"]
        if source == "benchmark_contract":
            if expected is None:
                raise ContractError(f"{gate_where}.expected is required for a contract gate")
            parsed_expected: Any = _number(
                expected, f"{gate_where}.expected", nonnegative=True
            )
        else:
            if expected is not None:
                raise ContractError(
                    f"{gate_where}.expected must be null for a certification threshold"
                )
            parsed_expected = None
        gates.append(
            PerformanceGate(
                gate_id=_identifier(gate_obj["id"], f"{gate_where}.id"),
                metric=metric,
                operator=operator,
                threshold_source=source,
                expected=parsed_expected,
                decision=decision,
            )
        )
    gate_ids = [item.gate_id for item in gates]
    if not gates or len(gate_ids) != len(set(gate_ids)):
        raise ContractError(f"{where}.gates must be non-empty and have unique IDs")
    boundaries = _strings(obj["claim_boundaries"], f"{where}.claim_boundaries")
    return PerformanceLane(
        lane_id=_identifier(obj["id"], f"{where}.id"),
        description=_string(obj["description"], f"{where}.description"),
        activation=activation,
        session_mode=_identifier(obj["session_mode"], f"{where}.session_mode"),
        claim_types=claim_types,
        matrices=matrices,
        protocol=_parse_protocol(obj["protocol"], f"{where}.protocol"),
        metrics=tuple(metrics),
        gates=tuple(gates),
        required_artifacts=_strings(
            obj["required_artifacts"], f"{where}.required_artifacts", identifiers=True
        ),
        claim_boundaries=boundaries,
    )


class PerformanceContract:
    """Deep interface for performance planning and fail-closed evidence validation."""

    def __init__(
        self,
        *,
        contract_id: str,
        description: str,
        source_revision: str,
        source_clean_required: bool,
        environment_identity_fields: Tuple[str, ...],
        host_condition_fields: Tuple[str, ...],
        host_admission_limits: Mapping[str, float],
        benchmark_custody: Tuple[str, ...],
        common_required_artifacts: Tuple[str, ...],
        lanes: Tuple[PerformanceLane, ...],
        source_path: Path,
    ) -> None:
        self.contract_id = contract_id
        self.description = description
        self.source_revision = source_revision
        self.source_clean_required = source_clean_required
        self.environment_identity_fields = environment_identity_fields
        self.host_condition_fields = host_condition_fields
        self.host_admission_limits = host_admission_limits
        self.benchmark_custody = benchmark_custody
        self.common_required_artifacts = common_required_artifacts
        self.lanes = lanes
        self.source_path = source_path
        self.sha256 = sha256_file(source_path)

    @classmethod
    def load(cls, path: Path) -> "PerformanceContract":
        source_path = path.resolve()
        obj = _read_json(source_path, "performance contract")
        keys = [
            "schema_version",
            "id",
            "description",
            "source_contract",
            "publication_policy",
            "environment_requirements",
            "common_required_artifacts",
            "lanes",
        ]
        _strict_keys(obj, keys, [], str(source_path))
        if obj["schema_version"] != CONTRACT_SCHEMA:
            raise ContractError(f"{source_path}.schema_version must be {CONTRACT_SCHEMA!r}")
        source = _object(obj["source_contract"], f"{source_path}.source_contract")
        _strict_keys(source, ["repository", "revision", "clean_required"], [], "source_contract")
        if source["repository"] != "TurnVector":
            raise ContractError("performance contract source repository must be TurnVector")
        source_revision = _digest(source["revision"], "source_contract.revision", length=40)
        source_clean_required = _boolean(
            source["clean_required"], "source_contract.clean_required"
        )
        if not source_clean_required:
            raise ContractError("performance contract requires a clean source checkout")
        policy = _object(obj["publication_policy"], f"{source_path}.publication_policy")
        policy_keys = [
            "cross_mode_comparison",
            "unsupported_result",
            "thresholds_frozen_before_run",
            "raw_trials_required",
            "summary_recomputed",
            "percentile_method",
        ]
        _strict_keys(policy, policy_keys, [], "publication_policy")
        expected_policy = {
            "cross_mode_comparison": "forbidden_without_shared_denominator",
            "unsupported_result": "explicit_not_publishable",
            "thresholds_frozen_before_run": True,
            "raw_trials_required": True,
            "summary_recomputed": True,
            "percentile_method": "nearest_rank",
        }
        if policy != expected_policy:
            raise ContractError("publication_policy must match the fail-closed v1 policy")
        environment = _object(
            obj["environment_requirements"], f"{source_path}.environment_requirements"
        )
        _strict_keys(
            environment,
            [
                "identity_fields",
                "host_condition_fields",
                "admission_limits",
                "benchmark_custody",
            ],
            [],
            "environment_requirements",
        )
        identity_fields = _strings(
            environment["identity_fields"],
            "environment_requirements.identity_fields",
            identifiers=True,
        )
        host_fields = _strings(
            environment["host_condition_fields"],
            "environment_requirements.host_condition_fields",
            identifiers=True,
        )
        if set(identity_fields) & set(host_fields):
            raise ContractError("environment identity and host condition fields must not overlap")
        if identity_fields != ENVIRONMENT_IDENTITY_FIELDS:
            raise ContractError(
                "environment_requirements.identity_fields must match the v1 identity contract"
            )
        if host_fields != HOST_CONDITION_FIELDS:
            raise ContractError(
                "environment_requirements.host_condition_fields must match the v1 host contract"
            )
        raw_admission_limits = _object(
            environment["admission_limits"],
            "environment_requirements.admission_limits",
        )
        admission_limit_fields = (
            "max_load_average_1m",
            "max_top_process_cpu_percent",
        )
        _strict_keys(
            raw_admission_limits,
            admission_limit_fields,
            [],
            "environment_requirements.admission_limits",
        )
        admission_limits = {
            field: _number(
                raw_admission_limits[field],
                f"environment_requirements.admission_limits.{field}",
                nonnegative=True,
            )
            for field in admission_limit_fields
        }
        custody = _strings(
            environment["benchmark_custody"],
            "environment_requirements.benchmark_custody",
            identifiers=True,
        )
        if custody != BENCHMARK_CUSTODY_FIELDS:
            raise ContractError(
                "environment_requirements.benchmark_custody must match the v1 custody contract"
            )
        common_artifacts = _strings(
            obj["common_required_artifacts"],
            f"{source_path}.common_required_artifacts",
            identifiers=True,
        )
        if common_artifacts != COMMON_REQUIRED_ARTIFACTS:
            raise ContractError(
                "common_required_artifacts must match the v1 artifact contract"
            )
        lanes = tuple(
            _parse_lane(raw, f"{source_path}.lanes[{index}]")
            for index, raw in enumerate(_array(obj["lanes"], f"{source_path}.lanes"))
        )
        lane_ids = [lane.lane_id for lane in lanes]
        modes = [lane.session_mode for lane in lanes]
        if not lanes or len(lane_ids) != len(set(lane_ids)):
            raise ContractError("performance lanes must be non-empty and have unique IDs")
        if len(modes) != len(set(modes)):
            raise ContractError("each performance lane must own one unique session mode")
        for lane in lanes:
            overlap = set(common_artifacts) & set(lane.required_artifacts)
            if overlap:
                raise ContractError(
                    f"lane {lane.lane_id!r} repeats common artifacts: {sorted(overlap)!r}"
                )
        return cls(
            contract_id=_identifier(obj["id"], f"{source_path}.id"),
            description=_string(obj["description"], f"{source_path}.description"),
            source_revision=source_revision,
            source_clean_required=source_clean_required,
            environment_identity_fields=identity_fields,
            host_condition_fields=host_fields,
            host_admission_limits=admission_limits,
            benchmark_custody=custody,
            common_required_artifacts=common_artifacts,
            lanes=lanes,
            source_path=source_path,
        )

    def lane(self, lane_id: str) -> PerformanceLane:
        for lane in self.lanes:
            if lane.lane_id == lane_id:
                return lane
        raise ContractError(f"performance contract has no lane {lane_id!r}")

    def case_plan(self, lane_id: str) -> Tuple[PlannedPerformanceCase, ...]:
        lane = self.lane(lane_id)
        cases: List[PlannedPerformanceCase] = []
        ordinal = 0
        for matrix in lane.matrices:
            names = [dimension.dimension_id for dimension in matrix.dimensions]
            values = [dimension.values for dimension in matrix.dimensions]
            for combination in product(*values):
                ordinal += 1
                cases.append(
                    PlannedPerformanceCase(
                        case_id=f"{lane.lane_id}.{matrix.matrix_id}.{ordinal:04d}",
                        lane_id=lane.lane_id,
                        matrix_id=matrix.matrix_id,
                        ordinal=ordinal,
                        parameters=dict(zip(names, combination)),
                    )
                )
        if len(cases) != lane.case_count:
            raise ContractError(f"case plan for {lane_id!r} lost matrix combinations")
        return tuple(cases)

    def inspect(self) -> Mapping[str, Any]:
        lane_summaries = []
        for lane in self.lanes:
            lane_summaries.append(
                {
                    "lane_id": lane.lane_id,
                    "activation": lane.activation,
                    "session_mode": lane.session_mode,
                    "claim_types": list(lane.claim_types),
                    "case_count": lane.case_count,
                    "metric_count": len(lane.metrics),
                    "gate_count": len(lane.gates),
                }
            )
        return {
            "schema_version": CONTRACT_SCHEMA,
            "status": "valid",
            "contract_id": self.contract_id,
            "contract_sha256": self.sha256,
            "lane_count": len(self.lanes),
            "core_lane_count": sum(lane.activation == "core" for lane in self.lanes),
            "capability_conditioned_lane_count": sum(
                lane.activation == "capability_conditioned" for lane in self.lanes
            ),
            "planned_case_count": sum(lane.case_count for lane in self.lanes),
            "metric_count": sum(len(lane.metrics) for lane in self.lanes),
            "gate_count": sum(len(lane.gates) for lane in self.lanes),
            "host_admission_limits": dict(self.host_admission_limits),
            "lanes": lane_summaries,
        }

    def validate_artifact(self, path: Path) -> Mapping[str, Any]:
        artifact_path = path.resolve()
        obj = _read_json(artifact_path, "performance evidence")
        keys = [
            "schema_version",
            "contract",
            "lane_id",
            "support",
            "session",
            "environment",
            "custody",
            "protocol",
            "frozen_thresholds",
            "cases",
            "artifacts",
            "publication",
        ]
        _strict_keys(obj, keys, [], str(artifact_path))
        if obj["schema_version"] != EVIDENCE_SCHEMA:
            raise ContractError(f"{artifact_path}.schema_version must be {EVIDENCE_SCHEMA!r}")
        contract = _object(obj["contract"], f"{artifact_path}.contract")
        _strict_keys(contract, ["id", "sha256"], [], "evidence.contract")
        if contract["id"] != self.contract_id or contract["sha256"] != self.sha256:
            raise ContractError("performance evidence is bound to a different contract identity")
        lane_id = _identifier(obj["lane_id"], "evidence.lane_id")
        lane = self.lane(lane_id)
        support = self._validate_support(obj["support"], lane)
        session, evidence_reasons = self._validate_session(obj["session"], lane)
        evidence_reasons.extend(self._validate_environment(obj["environment"]))
        custody = _strings(obj["custody"], "evidence.custody", identifiers=True)
        if custody != self.benchmark_custody:
            raise ContractError(
                f"evidence.custody must exactly match {self.benchmark_custody!r}"
            )
        protocol = _object(obj["protocol"], "evidence.protocol")
        if protocol != lane.protocol.as_dict():
            raise ContractError("evidence.protocol does not exactly match the lane protocol")
        if support == "unsupported":
            if obj["frozen_thresholds"] != {} or obj["cases"] != []:
                raise ContractError(
                    "unsupported capability evidence cannot contain thresholds or cases"
                )
            verified_artifacts = self._validate_artifacts(
                artifact_path, obj["artifacts"], ("run_manifest", "environment")
            )
            derived = {
                "evidence_status": "unsupported",
                "promotion_status": "not_evaluated",
                "candidate": False,
                "evidence_reasons": ["capability_unsupported"],
                "promotion_reasons": [],
            }
            supersedes = self._validate_publication(obj["publication"], derived)
            return self._report(
                evidence_path=artifact_path,
                lane=lane,
                session=session,
                status="unsupported",
                case_count=0,
                trial_count=0,
                artifact_count=len(verified_artifacts),
                gate_results=(),
                derived=derived,
                supersedes=supersedes,
            )
        thresholds = self._validate_thresholds(obj["frozen_thresholds"], lane)
        summaries, trial_count = self._validate_cases(obj["cases"], lane)
        verified_artifacts = self._validate_artifacts(
            artifact_path,
            obj["artifacts"],
            self.common_required_artifacts + lane.required_artifacts,
        )
        self._validate_identity_artifact_links(obj["environment"], verified_artifacts)
        self._validate_certification_record(
            artifact_path,
            verified_artifacts,
            obj["environment"],
            session,
            lane,
            thresholds,
        )
        gate_results, gate_evidence_reasons, promotion_reasons = self._evaluate_gates(
            lane, summaries, thresholds
        )
        evidence_reasons.extend(gate_evidence_reasons)
        if evidence_reasons:
            evidence_status = "not_publishable"
            promotion_status = "not_evaluated"
            promotion_reasons = []
        else:
            evidence_status = "publishable"
            promotion_gates = [gate for gate in lane.gates if gate.decision == "promotion"]
            if not promotion_gates:
                promotion_status = "not_applicable"
            elif promotion_reasons:
                promotion_status = "failed"
            else:
                promotion_status = "passed"
        derived = {
            "evidence_status": evidence_status,
            "promotion_status": promotion_status,
            "candidate": evidence_status == "publishable",
            "evidence_reasons": evidence_reasons,
            "promotion_reasons": promotion_reasons,
        }
        supersedes = self._validate_publication(obj["publication"], derived)
        return self._report(
            evidence_path=artifact_path,
            lane=lane,
            session=session,
            status=evidence_status,
            case_count=len(summaries),
            trial_count=trial_count,
            artifact_count=len(verified_artifacts),
            gate_results=gate_results,
            derived=derived,
            supersedes=supersedes,
        )

    def _validate_support(self, value: Any, lane: PerformanceLane) -> str:
        obj = _object(value, "evidence.support")
        _strict_keys(obj, ["status", "reason"], [], "evidence.support")
        status = _identifier(obj["status"], "evidence.support.status")
        if status not in {"supported", "unsupported"}:
            raise ContractError("evidence.support.status must be supported or unsupported")
        reason = obj["reason"]
        if status == "supported":
            if reason is not None:
                raise ContractError("supported evidence must have a null support reason")
        else:
            if lane.activation != "capability_conditioned":
                raise ContractError("a core performance lane cannot be marked unsupported")
            _string(reason, "evidence.support.reason")
        return status

    def _validate_session(
        self, value: Any, lane: PerformanceLane
    ) -> Tuple[Mapping[str, Any], List[str]]:
        obj = _object(value, "evidence.session")
        keys = [
            "id",
            "mode",
            "claim_type",
            "subject_kind",
            "started_at",
            "finished_at",
            "source_revision",
            "source_dirty",
            "benchmark_revision",
            "benchmark_dirty",
            "comparison",
        ]
        _strict_keys(obj, keys, [], "evidence.session")
        _identifier(obj["id"], "evidence.session.id")
        if obj["mode"] != lane.session_mode:
            raise ContractError("evidence session mode does not match the selected lane")
        claim_type = _identifier(obj["claim_type"], "evidence.session.claim_type")
        if claim_type not in lane.claim_types:
            raise ContractError("evidence claim type is not allowed by the selected lane")
        subject_kind = _identifier(obj["subject_kind"], "evidence.session.subject_kind")
        if subject_kind not in {"implementation", "fixture"}:
            raise ContractError(
                "evidence.session.subject_kind must be implementation or fixture"
            )
        started = _timestamp(obj["started_at"], "evidence.session.started_at")
        finished = _timestamp(obj["finished_at"], "evidence.session.finished_at")
        if finished < started:
            raise ContractError("evidence session finished before it started")
        source_revision = _digest(
            obj["source_revision"], "evidence.session.source_revision", length=40
        )
        benchmark_revision = _digest(
            obj["benchmark_revision"], "evidence.session.benchmark_revision", length=40
        )
        source_dirty = _boolean(obj["source_dirty"], "evidence.session.source_dirty")
        benchmark_dirty = _boolean(
            obj["benchmark_dirty"], "evidence.session.benchmark_dirty"
        )
        comparison = obj["comparison"]
        if claim_type == "paired_delta":
            comparison_obj = _object(comparison, "evidence.session.comparison")
            _strict_keys(
                comparison_obj,
                [
                    "denominator_id",
                    "denominator_sha256",
                    "pairing_key_sha256",
                    "measurement_semantics",
                ],
                [],
                "evidence.session.comparison",
            )
            _string(comparison_obj["denominator_id"], "comparison.denominator_id")
            _digest(comparison_obj["denominator_sha256"], "comparison.denominator_sha256")
            _digest(comparison_obj["pairing_key_sha256"], "comparison.pairing_key_sha256")
            if comparison_obj["measurement_semantics"] != "same_session":
                raise ContractError(
                    "paired_delta evidence requires same_session measurement semantics"
                )
        elif comparison is not None:
            raise ContractError("non-paired evidence must have a null comparison")
        reasons: List[str] = []
        if source_revision != self.source_revision:
            reasons.append("source_revision_mismatch")
        if source_dirty:
            reasons.append("source_checkout_dirty")
        if benchmark_dirty:
            reasons.append("benchmark_checkout_dirty")
        if subject_kind == "fixture":
            reasons.append("fixture_subject")
        return (
            {
                "id": obj["id"],
                "mode": lane.session_mode,
                "claim_type": claim_type,
                "subject_kind": subject_kind,
                "started_at": obj["started_at"],
                "finished_at": obj["finished_at"],
                "source_revision": source_revision,
                "benchmark_revision": benchmark_revision,
            },
            reasons,
        )

    def _validate_environment(self, value: Any) -> List[str]:
        obj = _object(value, "evidence.environment")
        expected_fields = self.environment_identity_fields + self.host_condition_fields
        _strict_keys(obj, expected_fields, [], "evidence.environment")
        _string(obj["device_class"], "evidence.environment.device_class")
        _integer(obj["memory_bytes"], "evidence.environment.memory_bytes", minimum=1)
        _string(obj["os_build"], "evidence.environment.os_build")
        for field in (
            "resolved_runtime_sha256",
            "subject_binary_sha256",
            "model_manifest_sha256",
            "prompt_manifest_sha256",
            "certification_record_sha256",
        ):
            _digest(obj[field], f"evidence.environment.{field}")
        power_source = _identifier(
            obj["power_source"], "evidence.environment.power_source"
        )
        thermal_start = _identifier(
            obj["thermal_state_start"], "evidence.environment.thermal_state_start"
        )
        thermal_end = _identifier(
            obj["thermal_state_end"], "evidence.environment.thermal_state_end"
        )
        load_values = [
            _number(
                obj[field], f"evidence.environment.{field}", nonnegative=True
            )
            for field in ("load_average_1m_start", "load_average_1m_end")
        ]
        cpu_values = [
            _number(
                obj[field], f"evidence.environment.{field}", nonnegative=True
            )
            for field in (
                "top_process_cpu_percent_start",
                "top_process_cpu_percent_end",
            )
        ]
        admission = _boolean(
            obj["host_admission_passed"], "evidence.environment.host_admission_passed"
        )
        reasons = []
        if power_source != "external":
            reasons.append("host_not_on_external_power")
        if thermal_start != "nominal" or thermal_end != "nominal":
            reasons.append("host_thermal_state_not_nominal")
        if max(load_values) > self.host_admission_limits["max_load_average_1m"]:
            reasons.append("host_load_average_exceeded")
        if (
            max(cpu_values)
            > self.host_admission_limits["max_top_process_cpu_percent"]
        ):
            reasons.append("host_top_process_cpu_exceeded")
        if not admission:
            reasons.append("host_admission_failed")
        return reasons

    def _validate_thresholds(
        self, value: Any, lane: PerformanceLane
    ) -> Mapping[str, float]:
        obj = _object(value, "evidence.frozen_thresholds")
        expected = tuple(
            gate.gate_id
            for gate in lane.gates
            if gate.threshold_source == "certification_record"
        )
        _strict_keys(obj, expected, [], "evidence.frozen_thresholds")
        return {
            gate_id: _number(
                obj[gate_id],
                f"evidence.frozen_thresholds.{gate_id}",
                nonnegative=True,
            )
            for gate_id in expected
        }

    def _validate_cases(
        self, value: Any, lane: PerformanceLane
    ) -> Tuple[Mapping[str, Mapping[str, float]], int]:
        raw_cases = _array(value, "evidence.cases")
        plan = self.case_plan(lane.lane_id)
        if len(raw_cases) != len(plan):
            raise ContractError(
                f"evidence.cases must contain the exact {len(plan)}-case plan"
            )
        metric_ids = tuple(metric.metric_id for metric in lane.metrics)
        metric_by_id = {metric.metric_id: metric for metric in lane.metrics}
        summaries: Dict[str, Mapping[str, float]] = {}
        trial_count = 0
        for index, (raw, planned) in enumerate(zip(raw_cases, plan)):
            where = f"evidence.cases[{index}]"
            case = _object(raw, where)
            _strict_keys(
                case,
                ["case_id", "matrix_id", "parameters", "status", "trials", "summary"],
                [],
                where,
            )
            if (
                case["case_id"] != planned.case_id
                or case["matrix_id"] != planned.matrix_id
                or case["parameters"] != planned.parameters
            ):
                raise ContractError(f"{where} does not match the frozen case plan")
            if case["status"] != "measured":
                raise ContractError(f"{where}.status must be measured")
            trials = _array(case["trials"], f"{where}.trials")
            if len(trials) != lane.protocol.measured_repetitions:
                raise ContractError(
                    f"{where}.trials must contain exactly "
                    f"{lane.protocol.measured_repetitions} repetitions"
                )
            samples: Dict[str, List[float]] = {metric_id: [] for metric_id in metric_ids}
            for repetition, raw_trial in enumerate(trials):
                trial_where = f"{where}.trials[{repetition}]"
                trial = _object(raw_trial, trial_where)
                _strict_keys(trial, ["repetition", "metrics"], [], trial_where)
                if trial["repetition"] != repetition:
                    raise ContractError(f"{trial_where}.repetition must preserve trial order")
                metrics = _object(trial["metrics"], f"{trial_where}.metrics")
                _strict_keys(metrics, metric_ids, [], f"{trial_where}.metrics")
                for metric_id in metric_ids:
                    observed = _number(
                        metrics[metric_id],
                        f"{trial_where}.metrics.{metric_id}",
                        nonnegative=True,
                    )
                    if metric_by_id[metric_id].unit == "count" and not observed.is_integer():
                        raise ContractError(
                            f"{trial_where}.metrics.{metric_id} must be an integer count"
                        )
                    samples[metric_id].append(observed)
            summary = _object(case["summary"], f"{where}.summary")
            _strict_keys(summary, metric_ids, [], f"{where}.summary")
            recomputed: Dict[str, float] = {}
            for metric in lane.metrics:
                expected_value = _reduce(samples[metric.metric_id], metric.reducer)
                declared = _number(
                    summary[metric.metric_id], f"{where}.summary.{metric.metric_id}"
                )
                if not math.isclose(declared, expected_value, rel_tol=1e-9, abs_tol=1e-9):
                    raise ContractError(
                        f"{where}.summary.{metric.metric_id} does not recompute from raw trials"
                    )
                recomputed[metric.metric_id] = expected_value
            summaries[planned.case_id] = recomputed
            trial_count += len(trials)
        return summaries, trial_count

    def _validate_artifacts(
        self, evidence_path: Path, value: Any, required_ids: Tuple[str, ...]
    ) -> Mapping[str, Mapping[str, Any]]:
        raw_artifacts = _array(value, "evidence.artifacts")
        if len(raw_artifacts) != len(required_ids):
            raise ContractError(
                f"evidence.artifacts must contain exactly {list(required_ids)!r}"
            )
        observed_ids = []
        verified_by_id: Dict[str, Mapping[str, Any]] = {}
        for index, raw in enumerate(raw_artifacts):
            where = f"evidence.artifacts[{index}]"
            descriptor = _object(raw, where)
            _strict_keys(
                descriptor, ["id", "path", "size", "sha256", "custody"], [], where
            )
            artifact_id = _identifier(descriptor["id"], f"{where}.id")
            observed_ids.append(artifact_id)
            if descriptor["custody"] != "benchmark":
                raise ContractError(f"{where}.custody must be benchmark")
            verified = validate_subject_artifact(
                {
                    "id": artifact_id,
                    "path": descriptor["path"],
                    "size": descriptor["size"],
                    "sha256": descriptor["sha256"],
                },
                evidence_path.parent,
            )
            verified_by_id[artifact_id] = verified
        if tuple(observed_ids) != required_ids:
            raise ContractError(
                f"evidence artifact IDs must preserve required order {required_ids!r}"
            )
        return verified_by_id

    @staticmethod
    def _validate_identity_artifact_links(
        environment: Any, artifacts: Mapping[str, Mapping[str, Any]]
    ) -> None:
        environment_obj = _object(environment, "evidence.environment")
        links = {
            "model_manifest": "model_manifest_sha256",
            "prompt_manifest": "prompt_manifest_sha256",
            "certification_record": "certification_record_sha256",
        }
        for artifact_id, environment_field in links.items():
            if artifacts[artifact_id]["sha256"] != environment_obj[environment_field]:
                raise ContractError(
                    f"evidence.environment.{environment_field} does not match "
                    f"artifact {artifact_id!r}"
                )

    def _validate_certification_record(
        self,
        evidence_path: Path,
        artifacts: Mapping[str, Mapping[str, Any]],
        environment: Any,
        session: Mapping[str, Any],
        lane: PerformanceLane,
        thresholds: Mapping[str, float],
    ) -> None:
        descriptor = artifacts["certification_record"]
        path = evidence_path.parent / str(descriptor["path"])
        obj = _read_json(path, "performance certification record")
        keys = [
            "schema_version",
            "id",
            "contract",
            "lane_id",
            "issued_at",
            "expires_at",
            "identity",
            "thresholds",
        ]
        _strict_keys(obj, keys, [], str(path))
        if obj["schema_version"] != CERTIFICATION_SCHEMA:
            raise ContractError(
                f"{path}.schema_version must be {CERTIFICATION_SCHEMA!r}"
            )
        _identifier(obj["id"], f"{path}.id")
        contract = _object(obj["contract"], f"{path}.contract")
        _strict_keys(contract, ["id", "sha256"], [], f"{path}.contract")
        if contract != {"id": self.contract_id, "sha256": self.sha256}:
            raise ContractError("performance certification record contract identity differs")
        if obj["lane_id"] != lane.lane_id:
            raise ContractError("performance certification record applies to a different lane")
        issued_at = _timestamp(obj["issued_at"], f"{path}.issued_at")
        expires_at = _timestamp(obj["expires_at"], f"{path}.expires_at")
        session_start = _timestamp(session["started_at"], "evidence.session.started_at")
        if issued_at > session_start or expires_at <= session_start:
            raise ContractError(
                "performance certification record was not applicable at session start"
            )
        environment_obj = _object(environment, "evidence.environment")
        identity = _object(obj["identity"], f"{path}.identity")
        identity_fields = [
            "resolved_runtime_sha256",
            "subject_binary_sha256",
            "model_manifest_sha256",
            "prompt_manifest_sha256",
        ]
        _strict_keys(identity, identity_fields, [], f"{path}.identity")
        for field in identity_fields:
            _digest(identity[field], f"{path}.identity.{field}")
            if identity[field] != environment_obj[field]:
                raise ContractError(
                    f"performance certification record identity {field!r} differs"
                )
        raw_thresholds = _object(obj["thresholds"], f"{path}.thresholds")
        _strict_keys(raw_thresholds, tuple(thresholds), [], f"{path}.thresholds")
        certified_thresholds = {
            gate_id: _number(
                raw_thresholds[gate_id],
                f"{path}.thresholds.{gate_id}",
                nonnegative=True,
            )
            for gate_id in thresholds
        }
        if certified_thresholds != thresholds:
            raise ContractError(
                "frozen thresholds do not match the performance certification record"
            )

    def _evaluate_gates(
        self,
        lane: PerformanceLane,
        summaries: Mapping[str, Mapping[str, float]],
        thresholds: Mapping[str, float],
    ) -> Tuple[Tuple[Mapping[str, Any], ...], List[str], List[str]]:
        results: List[Mapping[str, Any]] = []
        evidence_reasons: List[str] = []
        promotion_reasons: List[str] = []
        for gate in lane.gates:
            threshold: Any = (
                gate.expected
                if gate.threshold_source == "benchmark_contract"
                else thresholds[gate.gate_id]
            )
            failed_case_ids = [
                case_id
                for case_id, summary in summaries.items()
                if not _compare(summary[gate.metric], gate.operator, threshold)
            ]
            passed = not failed_case_ids
            results.append(
                {
                    "gate_id": gate.gate_id,
                    "metric": gate.metric,
                    "operator": gate.operator,
                    "threshold": threshold,
                    "decision": gate.decision,
                    "status": "passed" if passed else "failed",
                    "failed_case_ids": failed_case_ids,
                }
            )
            if not passed:
                reason = f"{gate.decision}_gate_failed:{gate.gate_id}"
                if gate.decision == "evidence":
                    evidence_reasons.append(reason)
                else:
                    promotion_reasons.append(reason)
        return tuple(results), evidence_reasons, promotion_reasons

    def _validate_publication(
        self, value: Any, derived: Mapping[str, Any]
    ) -> Tuple[str, ...]:
        obj = _object(value, "evidence.publication")
        keys = [
            "evidence_status",
            "promotion_status",
            "candidate",
            "evidence_reasons",
            "promotion_reasons",
            "supersedes",
        ]
        _strict_keys(obj, keys, [], "evidence.publication")
        supersedes = _strings(
            obj["supersedes"], "evidence.publication.supersedes", allow_empty=True
        )
        evidence_status = _identifier(
            obj["evidence_status"], "evidence.publication.evidence_status"
        )
        promotion_status = _identifier(
            obj["promotion_status"], "evidence.publication.promotion_status"
        )
        if evidence_status not in {"publishable", "not_publishable", "unsupported"}:
            raise ContractError("evidence.publication.evidence_status is invalid")
        if promotion_status not in {
            "passed",
            "failed",
            "not_applicable",
            "not_evaluated",
        }:
            raise ContractError("evidence.publication.promotion_status is invalid")
        evidence_reasons = _strings(
            obj["evidence_reasons"],
            "evidence.publication.evidence_reasons",
            allow_empty=True,
        )
        promotion_reasons = _strings(
            obj["promotion_reasons"],
            "evidence.publication.promotion_reasons",
            allow_empty=True,
        )
        declared = {
            "evidence_status": evidence_status,
            "promotion_status": promotion_status,
            "candidate": _boolean(
                obj["candidate"], "evidence.publication.candidate"
            ),
            "evidence_reasons": list(evidence_reasons),
            "promotion_reasons": list(promotion_reasons),
        }
        if declared != derived:
            raise ContractError(
                "declared publication decision differs from the independently derived decision"
            )
        return supersedes

    def _report(
        self,
        *,
        evidence_path: Path,
        lane: PerformanceLane,
        session: Mapping[str, Any],
        status: str,
        case_count: int,
        trial_count: int,
        artifact_count: int,
        gate_results: Sequence[Mapping[str, Any]],
        derived: Mapping[str, Any],
        supersedes: Sequence[str],
    ) -> Mapping[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA,
            "status": status,
            "contract_id": self.contract_id,
            "contract_sha256": self.sha256,
            "evidence_path": str(evidence_path),
            "evidence_sha256": sha256_file(evidence_path),
            "lane_id": lane.lane_id,
            "activation": lane.activation,
            "session_mode": lane.session_mode,
            "claim_type": session["claim_type"],
            "case_count": case_count,
            "trial_count": trial_count,
            "artifact_count": artifact_count,
            "evidence_status": derived["evidence_status"],
            "promotion_status": derived["promotion_status"],
            "publication_candidate": derived["candidate"],
            "evidence_reasons": list(derived["evidence_reasons"]),
            "promotion_reasons": list(derived["promotion_reasons"]),
            "supersedes": list(supersedes),
            "gates": list(gate_results),
            "claim_boundaries": list(lane.claim_boundaries),
        }


def _reduce(values: Sequence[float], reducer: str) -> float:
    if not values:
        raise ContractError("cannot reduce an empty performance sample set")
    if reducer == "median":
        return float(statistics.median(values))
    if reducer == "p95":
        return _nearest_rank(values, 0.95)
    if reducer == "p99":
        return _nearest_rank(values, 0.99)
    if reducer == "min":
        return min(values)
    if reducer == "max":
        return max(values)
    if reducer == "sum":
        return sum(values)
    raise ContractError(f"unsupported performance reducer {reducer!r}")


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _compare(observed: float, operator: str, expected: Any) -> bool:
    if operator == "eq":
        return math.isclose(observed, float(expected), rel_tol=1e-9, abs_tol=1e-9)
    if operator == "gte":
        return observed >= float(expected)
    if operator == "lte":
        return observed <= float(expected)
    raise ContractError(f"unsupported performance gate operator {operator!r}")


def load_performance_contract(path: Path) -> PerformanceContract:
    return PerformanceContract.load(path)
