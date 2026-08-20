from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError, IDENTIFIER_RE
from ..evidence import sha256_file


CONTRACT_SCHEMA = "turnvector.benchmark.gateway-validation-contract.v1"
EVIDENCE_SCHEMA = "turnvector.benchmark.gateway-validation-evidence.v1"
REPORT_SCHEMA = "turnvector.benchmark.gateway-validation-report.v1"

EXPECTED_POLICY = {
    "raw_artifacts_required": True,
    "summary_recomputed": True,
    "predictions_separate_from_measurements": True,
    "fixture_status": "not_claimable_fixture",
    "ownership_change_authorized": False,
    "percentile_method": "nearest_rank",
}
EXPECTED_EVENT_KINDS = (
    "exchange_reserved",
    "request_accepted",
    "backend_ownership_acquired",
    "output_production_started",
    "backpressure_entered",
    "deadline_expired",
    "client_disconnected",
    "cancel_ordered",
    "terminal_observed",
    "request_state_release_accepted",
    "peer_request_progress",
    "http_last_byte_committed",
    "response_closed",
)
EXPECTED_COUNTER_IDS = (
    "device_executor_socket_waits",
    "unreserved_output_publications",
    "capacity_overruns",
    "duplicate_outputs",
    "replays",
    "retries",
    "resumes",
    "ownership_transfers",
    "backend_leaks",
    "gateway_resource_leaks",
)
EXPECTED_DIMENSIONS = (
    ("probe_path", ("kernel_uds", "production_data_plane")),
    ("concurrency", (1, 8, 32, 128)),
    ("wire_class", ("minimum", "profile_maximum")),
    ("process_state", ("process_cold", "process_warm")),
)
EXPECTED_STAGE_IDS = (
    "socket_ns", "connect_accept_ns", "peer_credential_ns",
    "hello_ns", "descriptor_validation_ns",
)
EXPECTED_ARTIFACTS = (
    "run_manifest",
    "lifecycle_trace",
    "transport_trials",
    "host_samples",
)
EXPECTED_LIFECYCLE_CASES = (
    ("fast-fit", "non_streaming", "fast", "fits", "complete", False, False),
    ("slow-fit", "streaming", "slow", "fits", "complete", True, True),
    ("stalled-fit", "streaming", "stalled", "fits", "bounded_close", True, True),
    (
        "stalled-overflow",
        "streaming",
        "stalled",
        "overflows",
        "bounded_cancel",
        False,
        False,
    ),
    (
        "disconnect-mid-stream",
        "streaming",
        "disconnect",
        "fits",
        "disconnect_cancel",
        False,
        False,
    ),
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


def _integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{where} must be an integer greater than or equal to {minimum}")
    return value


def _hex(value: Any, where: str, length: int) -> str:
    text = _string(value, where)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ContractError(f"{where} must be a lowercase {length}-character hex digest")
    return text


def _strings(value: Any, where: str) -> Tuple[str, ...]:
    parsed = tuple(
        _identifier(item, f"{where}[{index}]")
        for index, item in enumerate(_array(value, where))
    )
    if not parsed or len(parsed) != len(set(parsed)):
        raise ContractError(f"{where} must be non-empty and contain unique IDs")
    return parsed


def _scalar(value: Any, where: str) -> Any:
    if isinstance(value, bool) or isinstance(value, (str, int)):
        return value
    raise ContractError(f"{where} must be a string, integer, or boolean")


def _read_json(path: Path, kind: str) -> Dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read {kind} {path}: {error}") from error


@dataclass(frozen=True)
class SourceContract:
    repository: str
    path: str
    contract_id: str
    sha256: str


@dataclass(frozen=True)
class LifecycleCase:
    case_id: str
    response_mode: str
    reader_mode: str
    buffer_relation: str
    expected_outcome: str
    requires_decoupling: bool
    requires_peer_progress: bool


@dataclass(frozen=True)
class TransportDimension:
    dimension_id: str
    values: Tuple[Any, ...]


@dataclass(frozen=True)
class TransportProtocol:
    cold_process_warmups: int
    warm_process_warmups: int
    measured_repetitions: int
    cooldown_seconds: int
    order_policy: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cold_process_warmups": self.cold_process_warmups,
            "warm_process_warmups": self.warm_process_warmups,
            "measured_repetitions": self.measured_repetitions,
            "cooldown_seconds": self.cooldown_seconds,
            "order_policy": self.order_policy,
        }


@dataclass(frozen=True)
class PlannedTransportCase:
    case_id: str
    ordinal: int
    parameters: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "ordinal": self.ordinal,
            "parameters": dict(self.parameters),
        }


class GatewayValidationContract:
    """Deep Interface for Gateway case planning and evidence validation."""

    def __init__(
        self,
        *,
        contract_id: str,
        description: str,
        source_contract: SourceContract,
        lifecycle_cases: Tuple[LifecycleCase, ...],
        event_kinds: Tuple[str, ...],
        counter_ids: Tuple[str, ...],
        transport_dimensions: Tuple[TransportDimension, ...],
        transport_protocol: TransportProtocol,
        stage_ids: Tuple[str, ...],
        reuse_factors: Tuple[int, ...],
        required_artifacts: Tuple[str, ...],
        source_path: Path,
    ) -> None:
        self.contract_id = contract_id
        self.description = description
        self.source_contract = source_contract
        self.lifecycle_cases = lifecycle_cases
        self.event_kinds = event_kinds
        self.counter_ids = counter_ids
        self.transport_dimensions = transport_dimensions
        self.transport_protocol = transport_protocol
        self.stage_ids = stage_ids
        self.reuse_factors = reuse_factors
        self.required_artifacts = required_artifacts
        self.source_path = source_path
        self.sha256 = sha256_file(source_path)

    @classmethod
    def load(cls, path: Path) -> "GatewayValidationContract":
        source_path = path.resolve()
        obj = _read_json(source_path, "Gateway validation contract")
        keys = [
            "schema_version",
            "id",
            "description",
            "source_contract",
            "policy",
            "lifecycle",
            "transport",
            "required_artifacts",
        ]
        _strict_keys(obj, keys, [], str(source_path))
        if obj["schema_version"] != CONTRACT_SCHEMA:
            raise ContractError(f"{source_path}.schema_version must be {CONTRACT_SCHEMA!r}")
        source = _object(obj["source_contract"], "source_contract")
        _strict_keys(source, ["repository", "path", "id", "sha256"], [], "source_contract")
        if source["repository"] != "TurnVector":
            raise ContractError("Gateway source contract repository must be TurnVector")
        source_path_text = _string(source["path"], "source_contract.path")
        if Path(source_path_text).is_absolute() or ".." in Path(source_path_text).parts:
            raise ContractError("source_contract.path must be a contained relative path")
        policy = _object(obj["policy"], "policy")
        if policy != EXPECTED_POLICY:
            raise ContractError("policy must match the fail-closed Gateway v1 policy")

        lifecycle = _object(obj["lifecycle"], "lifecycle")
        _strict_keys(lifecycle, ["cases", "event_kinds", "counter_ids"], [], "lifecycle")
        lifecycle_cases = tuple(
            cls._parse_lifecycle_case(raw, index)
            for index, raw in enumerate(_array(lifecycle["cases"], "lifecycle.cases"))
        )
        case_ids = [case.case_id for case in lifecycle_cases]
        if not lifecycle_cases or len(case_ids) != len(set(case_ids)):
            raise ContractError("lifecycle.cases must be non-empty and have unique IDs")

        transport = _object(obj["transport"], "transport")
        _strict_keys(
            transport,
            ["dimensions", "protocol", "stage_ids", "reuse_factors"],
            [],
            "transport",
        )
        dimensions = tuple(
            cls._parse_dimension(raw, index)
            for index, raw in enumerate(_array(transport["dimensions"], "transport.dimensions"))
        )
        dimension_ids = [dimension.dimension_id for dimension in dimensions]
        if not dimensions or len(dimension_ids) != len(set(dimension_ids)):
            raise ContractError("transport.dimensions must be non-empty and have unique IDs")
        protocol = cls._parse_protocol(transport["protocol"])
        reuse_factors = tuple(
            _integer(raw, f"transport.reuse_factors[{index}]", minimum=2)
            for index, raw in enumerate(_array(transport["reuse_factors"], "transport.reuse_factors"))
        )
        if not reuse_factors or len(reuse_factors) != len(set(reuse_factors)):
            raise ContractError("transport.reuse_factors must be non-empty and unique")

        event_kinds = _strings(lifecycle["event_kinds"], "lifecycle.event_kinds")
        counter_ids = _strings(lifecycle["counter_ids"], "lifecycle.counter_ids")
        stage_ids = _strings(transport["stage_ids"], "transport.stage_ids")
        artifacts = _strings(obj["required_artifacts"], "required_artifacts")
        observed_dimensions = tuple(
            (dimension.dimension_id, dimension.values) for dimension in dimensions
        )
        observed_lifecycle_cases = tuple(
            (
                case.case_id,
                case.response_mode,
                case.reader_mode,
                case.buffer_relation,
                case.expected_outcome,
                case.requires_decoupling,
                case.requires_peer_progress,
            )
            for case in lifecycle_cases
        )
        if observed_lifecycle_cases != EXPECTED_LIFECYCLE_CASES:
            raise ContractError("lifecycle.cases must match Gateway v1")
        if event_kinds != EXPECTED_EVENT_KINDS:
            raise ContractError("lifecycle.event_kinds must match Gateway v1")
        if counter_ids != EXPECTED_COUNTER_IDS:
            raise ContractError("lifecycle.counter_ids must match Gateway v1")
        if observed_dimensions != EXPECTED_DIMENSIONS:
            raise ContractError("transport.dimensions must match Gateway v1")
        if stage_ids != EXPECTED_STAGE_IDS:
            raise ContractError("transport.stage_ids must match Gateway v1")
        if reuse_factors != (2, 8, 32):
            raise ContractError("transport.reuse_factors must match Gateway v1")
        if artifacts != EXPECTED_ARTIFACTS:
            raise ContractError("required_artifacts must match Gateway v1")
        if protocol != TransportProtocol(0, 100, 100, 1, "balanced"):
            raise ContractError("transport.protocol must match Gateway v1")

        return cls(
            contract_id=_identifier(obj["id"], f"{source_path}.id"),
            description=_string(obj["description"], f"{source_path}.description"),
            source_contract=SourceContract(
                repository="TurnVector",
                path=source_path_text,
                contract_id=_identifier(source["id"], "source_contract.id"),
                sha256=_hex(source["sha256"], "source_contract.sha256", 64),
            ),
            lifecycle_cases=lifecycle_cases,
            event_kinds=event_kinds,
            counter_ids=counter_ids,
            transport_dimensions=dimensions,
            transport_protocol=protocol,
            stage_ids=stage_ids,
            reuse_factors=reuse_factors,
            required_artifacts=artifacts,
            source_path=source_path,
        )

    @staticmethod
    def _parse_lifecycle_case(value: Any, index: int) -> LifecycleCase:
        where = f"lifecycle.cases[{index}]"
        obj = _object(value, where)
        keys = [
            "id",
            "response_mode",
            "reader_mode",
            "buffer_relation",
            "expected_outcome",
            "requires_decoupling",
            "requires_peer_progress",
        ]
        _strict_keys(obj, keys, [], where)
        return LifecycleCase(
            case_id=_identifier(obj["id"], f"{where}.id"),
            response_mode=_identifier(obj["response_mode"], f"{where}.response_mode"),
            reader_mode=_identifier(obj["reader_mode"], f"{where}.reader_mode"),
            buffer_relation=_identifier(obj["buffer_relation"], f"{where}.buffer_relation"),
            expected_outcome=_identifier(obj["expected_outcome"], f"{where}.expected_outcome"),
            requires_decoupling=_boolean(
                obj["requires_decoupling"], f"{where}.requires_decoupling"
            ),
            requires_peer_progress=_boolean(
                obj["requires_peer_progress"], f"{where}.requires_peer_progress"
            ),
        )

    @staticmethod
    def _parse_dimension(value: Any, index: int) -> TransportDimension:
        where = f"transport.dimensions[{index}]"
        obj = _object(value, where)
        _strict_keys(obj, ["id", "values"], [], where)
        values = tuple(
            _scalar(raw, f"{where}.values[{value_index}]")
            for value_index, raw in enumerate(_array(obj["values"], f"{where}.values"))
        )
        serialized = [json.dumps(item, sort_keys=True) for item in values]
        if not values or len(serialized) != len(set(serialized)):
            raise ContractError(f"{where}.values must be non-empty and unique")
        return TransportDimension(
            dimension_id=_identifier(obj["id"], f"{where}.id"), values=values
        )

    @staticmethod
    def _parse_protocol(value: Any) -> TransportProtocol:
        obj = _object(value, "transport.protocol")
        keys = [
            "cold_process_warmups",
            "warm_process_warmups",
            "measured_repetitions",
            "cooldown_seconds",
            "order_policy",
        ]
        _strict_keys(obj, keys, [], "transport.protocol")
        order = _identifier(obj["order_policy"], "transport.protocol.order_policy")
        if order != "balanced":
            raise ContractError("transport.protocol.order_policy must be balanced")
        return TransportProtocol(
            cold_process_warmups=_integer(
                obj["cold_process_warmups"], "transport.protocol.cold_process_warmups"
            ),
            warm_process_warmups=_integer(
                obj["warm_process_warmups"], "transport.protocol.warm_process_warmups"
            ),
            measured_repetitions=_integer(
                obj["measured_repetitions"],
                "transport.protocol.measured_repetitions",
                minimum=1,
            ),
            cooldown_seconds=_integer(
                obj["cooldown_seconds"], "transport.protocol.cooldown_seconds"
            ),
            order_policy=order,
        )

    def transport_case_plan(self) -> Tuple[PlannedTransportCase, ...]:
        names = [dimension.dimension_id for dimension in self.transport_dimensions]
        values = [dimension.values for dimension in self.transport_dimensions]
        return tuple(
            PlannedTransportCase(
                case_id=f"gateway-uds.{ordinal:04d}",
                ordinal=ordinal,
                parameters=dict(zip(names, combination)),
            )
            for ordinal, combination in enumerate(product(*values), start=1)
        )

    def inspect(self, target_repo: Optional[Path] = None) -> Mapping[str, Any]:
        source_status = "not_checked"
        if target_repo is not None:
            candidate = target_repo.resolve() / self.source_contract.path
            if not candidate.is_file():
                raise ContractError(f"Gateway source contract is missing: {candidate}")
            if sha256_file(candidate) != self.source_contract.sha256:
                raise ContractError("Gateway source contract hash does not match the fixed contract")
            source_status = "matched"
        plan = self.transport_case_plan()
        return {
            "schema_version": CONTRACT_SCHEMA,
            "status": "valid",
            "contract_id": self.contract_id,
            "contract_sha256": self.sha256,
            "source_contract_id": self.source_contract.contract_id,
            "source_contract_sha256": self.source_contract.sha256,
            "source_contract_status": source_status,
            "lifecycle_case_count": len(self.lifecycle_cases),
            "transport_case_count": len(plan),
            "transport_trial_count": len(plan)
            * self.transport_protocol.measured_repetitions,
            "reuse_factors": list(self.reuse_factors),
            "ownership_change_authorized": False,
        }

    def validate_artifact(self, path: Path) -> Mapping[str, Any]:
        from .evidence import validate_gateway_evidence

        return validate_gateway_evidence(self, path)


def load_gateway_validation_contract(path: Path) -> GatewayValidationContract:
    return GatewayValidationContract.load(path)
