from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from ..core import ContractError
from ..evidence import validate_subject_artifact
from .contract import (
    GatewayValidationContract,
    _array,
    _boolean,
    _hex,
    _identifier,
    _integer,
    _object,
    _read_json,
    _strict_keys,
    _string,
)


SESSION_DIGEST_FIELDS = (
    "gateway_build_sha256",
    "daemon_build_sha256",
    "compatibility_profile_sha256",
    "route_manifest_sha256",
    "tokenizer_template_sha256",
    "model_revision_sha256",
    "data_plane_descriptor_sha256",
    "effective_limits_sha256",
)
LIMIT_FIELDS = (
    "http_write_no_progress_ns",
    "http_write_total_ns",
    "cancellation_completion_ns",
)


def number(value: Any, where: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed) or (nonnegative and parsed < 0):
        qualifier = " finite and non-negative" if nonnegative else " finite"
        raise ContractError(f"{where} must be{qualifier}")
    return parsed


def read_jsonl(path: Path, kind: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                raise ContractError(f"{kind} {path}:{line_number} contains a blank line")
            try:
                rows.append(_object(json.loads(line), f"{path}:{line_number}"))
            except json.JSONDecodeError as error:
                raise ContractError(
                    f"cannot parse {kind} {path}:{line_number}: {error}"
                ) from error
    except OSError as error:
        raise ContractError(f"cannot read {kind} {path}: {error}") from error
    if not rows:
        raise ContractError(f"{kind} {path} must not be empty")
    return rows


def validate_session(
    value: Any, contract: GatewayValidationContract
) -> Tuple[Mapping[str, Any], List[str]]:
    obj = _object(value, "evidence.session")
    keys = [
        "id",
        "subject_kind",
        "started_at",
        "finished_at",
        "turnvector_head_before",
        "turnvector_head_after",
        "turnvector_status_before",
        "turnvector_status_after",
        "benchmark_head_before",
        "benchmark_head_after",
        "benchmark_status_before",
        "benchmark_status_after",
        "source_contract_sha256",
        *SESSION_DIGEST_FIELDS,
        "clock_mode",
        "clock_calibration_sha256",
    ]
    _strict_keys(obj, keys, [], "evidence.session")
    _identifier(obj["id"], "evidence.session.id")
    subject_kind = _identifier(obj["subject_kind"], "evidence.session.subject_kind")
    if subject_kind not in {"implementation", "fixture"}:
        raise ContractError("evidence.session.subject_kind must be implementation or fixture")
    started = _timestamp(obj["started_at"], "evidence.session.started_at")
    finished = _timestamp(obj["finished_at"], "evidence.session.finished_at")
    if finished < started:
        raise ContractError("evidence session finished before it started")
    revisions = {
        field: _hex(obj[field], f"evidence.session.{field}", 40)
        for field in (
            "turnvector_head_before",
            "turnvector_head_after",
            "benchmark_head_before",
            "benchmark_head_after",
        )
    }
    statuses = {
        field: _status_lines(obj[field], f"evidence.session.{field}")
        for field in (
            "turnvector_status_before",
            "turnvector_status_after",
            "benchmark_status_before",
            "benchmark_status_after",
        )
    }
    source_digest = _hex(
        obj["source_contract_sha256"], "evidence.session.source_contract_sha256", 64
    )
    if source_digest != contract.source_contract.sha256:
        raise ContractError("evidence is bound to a different TurnVector Gateway contract")
    for field in SESSION_DIGEST_FIELDS:
        _hex(obj[field], f"evidence.session.{field}", 64)
    clock_mode = _identifier(obj["clock_mode"], "evidence.session.clock_mode")
    calibration = obj["clock_calibration_sha256"]
    if clock_mode != "single_monotonic":
        raise ContractError("Gateway validation v1 requires one monotonic clock domain")
    if calibration is not None:
        raise ContractError("single_monotonic evidence must not declare clock calibration")

    reasons: List[str] = []
    for repository in ("turnvector", "benchmark"):
        if revisions[f"{repository}_head_before"] != revisions[f"{repository}_head_after"]:
            reasons.append(f"{repository}_revision_changed")
        before = statuses[f"{repository}_status_before"]
        after = statuses[f"{repository}_status_after"]
        if before != after:
            reasons.append(f"{repository}_status_changed")
        if before or after:
            reasons.append(f"{repository}_checkout_dirty")
    return (
        {
            "id": obj["id"],
            "subject_kind": subject_kind,
            "started_at": obj["started_at"],
            "finished_at": obj["finished_at"],
            "turnvector_revision": revisions["turnvector_head_before"],
            "benchmark_revision": revisions["benchmark_head_before"],
            "clock_mode": clock_mode,
        },
        reasons,
    )


def validate_environment(value: Any) -> Tuple[Mapping[str, Any], List[str]]:
    obj = _object(value, "evidence.environment")
    keys = [
        "hardware_id",
        "memory_bytes",
        "os_build",
        "power_source",
        "thermal_state_start",
        "thermal_state_end",
        "host_admission_passed",
    ]
    _strict_keys(obj, keys, [], "evidence.environment")
    environment = {
        "hardware_id": _string(obj["hardware_id"], "evidence.environment.hardware_id"),
        "memory_bytes": _integer(
            obj["memory_bytes"], "evidence.environment.memory_bytes", minimum=1
        ),
        "os_build": _string(obj["os_build"], "evidence.environment.os_build"),
        "power_source": _identifier(
            obj["power_source"], "evidence.environment.power_source"
        ),
        "thermal_state_start": _identifier(
            obj["thermal_state_start"], "evidence.environment.thermal_state_start"
        ),
        "thermal_state_end": _identifier(
            obj["thermal_state_end"], "evidence.environment.thermal_state_end"
        ),
        "host_admission_passed": _boolean(
            obj["host_admission_passed"], "evidence.environment.host_admission_passed"
        ),
    }
    reasons = []
    if environment["power_source"] != "external":
        reasons.append("host_not_on_external_power")
    if (
        environment["thermal_state_start"] != "nominal"
        or environment["thermal_state_end"] != "nominal"
    ):
        reasons.append("host_thermal_state_not_nominal")
    if not environment["host_admission_passed"]:
        reasons.append("host_admission_failed")
    return environment, reasons


def validate_limits(value: Any) -> Mapping[str, int]:
    obj = _object(value, "evidence.effective_limits")
    _strict_keys(obj, LIMIT_FIELDS, [], "evidence.effective_limits")
    return {
        field: _integer(obj[field], f"evidence.effective_limits.{field}", minimum=1)
        for field in LIMIT_FIELDS
    }


def validate_artifacts(
    contract: GatewayValidationContract, evidence_path: Path, value: Any
) -> Mapping[str, Mapping[str, Any]]:
    rows = _array(value, "evidence.artifacts")
    if len(rows) != len(contract.required_artifacts):
        raise ContractError("evidence.artifacts does not match the required artifact count")
    verified: Dict[str, Mapping[str, Any]] = {}
    observed_ids: List[str] = []
    for index, raw in enumerate(rows):
        where = f"evidence.artifacts[{index}]"
        descriptor = _object(raw, where)
        _strict_keys(descriptor, ["id", "path", "size", "sha256", "custody"], [], where)
        artifact_id = _identifier(descriptor["id"], f"{where}.id")
        if descriptor["custody"] != "benchmark":
            raise ContractError(f"{where}.custody must be benchmark")
        observed_ids.append(artifact_id)
        verified[artifact_id] = validate_subject_artifact(
            {
                "id": artifact_id,
                "path": descriptor["path"],
                "size": descriptor["size"],
                "sha256": descriptor["sha256"],
            },
            evidence_path.parent,
        )
    if tuple(observed_ids) != contract.required_artifacts:
        raise ContractError(
            f"evidence artifact IDs must preserve {contract.required_artifacts!r}"
        )
    return verified


def artifact_path(evidence_path: Path, descriptor: Mapping[str, Any]) -> Path:
    return evidence_path.parent / str(descriptor["path"])


def validate_host_samples(path: Path) -> None:
    rows = read_jsonl(path, "Gateway host samples")
    prior = -1
    for index, raw in enumerate(rows):
        where = f"host_samples[{index}]"
        _strict_keys(
            raw,
            ["at_ns", "load_average_1m", "top_process_cpu_percent", "thermal_state"],
            [],
            where,
        )
        at_ns = _integer(raw["at_ns"], f"{where}.at_ns")
        if at_ns < prior:
            raise ContractError(f"{where}.at_ns must be monotonic")
        prior = at_ns
        number(raw["load_average_1m"], f"{where}.load_average_1m", nonnegative=True)
        number(
            raw["top_process_cpu_percent"],
            f"{where}.top_process_cpu_percent",
            nonnegative=True,
        )
        _identifier(raw["thermal_state"], f"{where}.thermal_state")


def validate_run_manifest(
    path: Path,
    contract: GatewayValidationContract,
    session: Mapping[str, Any],
    limits: Mapping[str, int],
) -> None:
    obj = _read_json(path, "Gateway run manifest")
    keys = [
        "schema_version",
        "contract",
        "source_contract_sha256",
        "session_id",
        "effective_limits",
        "transport_protocol",
        "lifecycle_case_ids",
        "transport_case_plan",
    ]
    _strict_keys(obj, keys, [], str(path))
    if obj["schema_version"] != "turnvector.benchmark.gateway-validation-run-manifest.v1":
        raise ContractError("Gateway run manifest has an unsupported schema version")
    if obj["contract"] != {"id": contract.contract_id, "sha256": contract.sha256}:
        raise ContractError("Gateway run manifest contract identity differs")
    if obj["source_contract_sha256"] != contract.source_contract.sha256:
        raise ContractError("Gateway run manifest source contract identity differs")
    if obj["session_id"] != session["id"]:
        raise ContractError("Gateway run manifest session identity differs")
    if obj["effective_limits"] != dict(limits):
        raise ContractError("Gateway run manifest effective limits differ")
    if obj["transport_protocol"] != contract.transport_protocol.as_dict():
        raise ContractError("Gateway run manifest transport protocol differs")
    if obj["lifecycle_case_ids"] != [case.case_id for case in contract.lifecycle_cases]:
        raise ContractError("Gateway run manifest lifecycle CasePlan differs")
    if obj["transport_case_plan"] != [
        case.as_dict() for case in contract.transport_case_plan()
    ]:
        raise ContractError("Gateway run manifest transport CasePlan differs")


def _timestamp(value: Any, where: str) -> datetime:
    text = _string(value, where)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{where} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{where} must include a timezone")
    return parsed


def _status_lines(value: Any, where: str) -> Tuple[str, ...]:
    lines = tuple(
        _string(item, f"{where}[{index}]")
        for index, item in enumerate(_array(value, where))
    )
    if len(lines) != len(set(lines)):
        raise ContractError(f"{where} must not contain duplicates")
    return lines
