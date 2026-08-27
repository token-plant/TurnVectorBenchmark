from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Sequence

from .core import ContractError


def _record(evidence: Sequence[Mapping[str, Any]], lane_id: str) -> Mapping[str, Any]:
    records = [item["record"] for item in evidence if set(item) == {"record"}]
    if len(records) != 1 or not isinstance(records[0], dict):
        raise ContractError(
            f"{lane_id} must emit exactly one normalized raw record across its case steps"
        )
    return records[0]


def _keys(value: Any, required: Sequence[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != set(required):
        raise ContractError(f"{where} must contain exactly {sorted(required)!r}")
    return value


def _list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{where} must be finite")
    return result


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where} must be boolean")
    return value


def _empty_close(close: Mapping[str, Any], lane_id: str) -> None:
    if close:
        raise ContractError(
            f"{lane_id} case_close observations must be empty; metrics come from raw evidence"
        )


def core_event_replay(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "core-event-replay"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        ["first_state_hash", "replay_state_hash", "invariants", "transition_committed", "effects"],
        lane,
    )
    invariants = _list(value["invariants"], f"{lane}.invariants")
    committed = _boolean(value["transition_committed"], f"{lane}.transition_committed")
    effects = _list(value["effects"], f"{lane}.effects")
    return {
        "replay_hash_mismatches": int(value["first_state_hash"] != value["replay_state_hash"]),
        "committed_invariant_violations": sum(
            committed and not _boolean(item, f"{lane}.invariants") for item in invariants
        ),
        "failed_transition_effects": len(effects) if not committed else 0,
    }


def scheduler_performance(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "scheduler-performance"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        ["plan_pairs", "decision_latency_us", "decision_work", "driver_ipc_included"],
        lane,
    )
    pairs = _list(value["plan_pairs"], f"{lane}.plan_pairs")
    mismatches = 0
    for index, pair in enumerate(pairs):
        parsed = _keys(pair, ["expected", "observed"], f"{lane}.plan_pairs[{index}]")
        mismatches += parsed["expected"] != parsed["observed"]
    samples = [
        _number(item, f"{lane}.decision_latency_us[{index}]")
        for index, item in enumerate(_list(value["decision_latency_us"], f"{lane}.decision_latency_us"))
    ]
    work = _keys(value["decision_work"], ["operations", "seconds"], f"{lane}.decision_work")
    return {
        "performance_plan_mismatches": mismatches,
        "decision_latency_samples_us": samples,
        "decision_work": {
            "operations": _number(work["operations"], f"{lane}.decision_work.operations"),
            "seconds": _number(work["seconds"], f"{lane}.decision_work.seconds"),
        },
        "driver_ipc_observed": _boolean(
            value["driver_ipc_included"], f"{lane}.driver_ipc_included"
        ),
    }


def request_serving(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    lane = "request-serving-lifecycle"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        [
            "lifecycle",
            "outputs",
            "client_event",
            "acceptance_reserved",
            "acceptance_backend_handle",
            "cancellation_commit_sequence",
            "receipt_commit_sequence",
            "disconnect_observed",
            "backpressure_timeout_observed",
            "terminal_status_emitted",
        ],
        lane,
    )
    if value["client_event"] != parameters.get("client_event"):
        raise ContractError(f"{lane}.client_event differs from the CasePlan")
    lifecycle = _list(value["lifecycle"], f"{lane}.lifecycle")
    order = {name: index for index, name in enumerate(
        ["accepted", "preparing", "warming", "admitted", "materialized", "queued", "running", "terminal"]
    )}
    illegal = 0
    if not lifecycle or lifecycle[0] != "accepted":
        illegal += 1
    previous = -1
    for state in lifecycle:
        current = order.get(state)
        if current is None or current < previous:
            illegal += 1
        elif current is not None:
            previous = current
    if "materialized" in lifecycle and (
        "admitted" not in lifecycle or lifecycle.index("materialized") < lifecycle.index("admitted")
    ):
        illegal += 1
    illegal += _boolean(value["acceptance_reserved"], f"{lane}.acceptance_reserved")
    illegal += _boolean(
        value["acceptance_backend_handle"], f"{lane}.acceptance_backend_handle"
    )
    publications = []
    unreserved = 0
    for index, raw in enumerate(_list(value["outputs"], f"{lane}.outputs")):
        output = _keys(raw, ["publication_id", "sequence", "reserved"], f"{lane}.outputs[{index}]")
        publications.append((output["publication_id"], output["sequence"]))
        unreserved += not _boolean(output["reserved"], f"{lane}.outputs[{index}].reserved")
    duplicates = len(publications) - len(set(publications))
    terminal_emitted = _boolean(
        value["terminal_status_emitted"], f"{lane}.terminal_status_emitted"
    )
    missing_terminal = int(not terminal_emitted or "terminal" not in lifecycle)
    disconnect = _boolean(value["disconnect_observed"], f"{lane}.disconnect_observed")
    backpressure = _boolean(
        value["backpressure_timeout_observed"],
        f"{lane}.backpressure_timeout_observed",
    )

    def sequence(name: str) -> Any:
        raw = value[name]
        if raw is None:
            return None
        parsed = _number(raw, f"{lane}.{name}")
        if parsed <= 0 or not parsed.is_integer():
            raise ContractError(f"{lane}.{name} must be a positive integer or null")
        return int(parsed)

    cancellation = sequence("cancellation_commit_sequence")
    receipt = sequence("receipt_commit_sequence")
    client_event = value["client_event"]
    event_violations = 0
    if client_event == "none":
        event_violations += cancellation is not None or disconnect or backpressure
    elif client_event == "cancel_before_receipt":
        event_violations += (
            cancellation is None
            or (receipt is not None and cancellation >= receipt)
            or disconnect
            or backpressure
        )
    elif client_event == "cancel_after_receipt":
        event_violations += (
            cancellation is None
            or receipt is None
            or cancellation <= receipt
            or disconnect
            or backpressure
        )
    elif client_event == "disconnect":
        event_violations += cancellation is None or not disconnect or backpressure
    elif client_event == "backpressure_timeout":
        event_violations += cancellation is None or not disconnect or not backpressure
    else:
        raise ContractError(f"{lane}.client_event is invalid")
    return {
        "illegal_lifecycle_transitions": illegal,
        "duplicate_output_publications": duplicates,
        "unreserved_output_publications": unreserved,
        "missing_terminal_status": missing_terminal,
        "client_event_violations": int(event_violations),
    }


def mlx_fixture(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "mlx-native-correctness"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        ["parity", "owner_thread_id", "execution_thread_ids", "cross_thread_rejected"],
        lane,
    )
    mismatch = {"output": 0, "logits": 0, "kv": 0}
    for index, raw in enumerate(_list(value["parity"], f"{lane}.parity")):
        pair = _keys(raw, ["kind", "expected_sha256", "observed_sha256"], f"{lane}.parity[{index}]")
        if pair["kind"] not in mismatch:
            raise ContractError(f"{lane}.parity[{index}].kind is invalid")
        mismatch[str(pair["kind"])] += pair["expected_sha256"] != pair["observed_sha256"]
    owner = value["owner_thread_id"]
    thread_violations = sum(
        item != owner for item in _list(value["execution_thread_ids"], f"{lane}.execution_thread_ids")
    )
    thread_violations += not _boolean(
        value["cross_thread_rejected"], f"{lane}.cross_thread_rejected"
    )
    return {
        "output_mismatches": mismatch["output"],
        "complete_logits_mismatches": mismatch["logits"],
        "kv_state_mismatches": mismatch["kv"],
        "owner_thread_violations": thread_violations,
    }


def bounded_turn(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "bounded-turn-and-ffi"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        ["turn_service_us", "ffi_pairs", "ffi_baseline_us", "ffi_candidate_us", "native_outcomes"],
        lane,
    )
    pairs = _list(value["ffi_pairs"], f"{lane}.ffi_pairs")
    mismatches = sum(
        _keys(pair, ["expected", "observed"], f"{lane}.ffi_pairs")["expected"]
        != _keys(pair, ["expected", "observed"], f"{lane}.ffi_pairs")["observed"]
        for pair in pairs
    )
    baselines = _list(value["ffi_baseline_us"], f"{lane}.ffi_baseline_us")
    candidates = _list(value["ffi_candidate_us"], f"{lane}.ffi_candidate_us")
    if len(baselines) != len(candidates) or not baselines:
        raise ContractError(f"{lane} FFI baseline/candidate samples must align")
    regressions = []
    for index, (baseline, candidate) in enumerate(zip(baselines, candidates)):
        base = _number(baseline, f"{lane}.ffi_baseline_us[{index}]")
        observed = _number(candidate, f"{lane}.ffi_candidate_us[{index}]")
        if base <= 0:
            raise ContractError(f"{lane} FFI baseline must be positive")
        regressions.append((observed - base) / base * 100.0)
    cleanup = 0
    for index, raw in enumerate(_list(value["native_outcomes"], f"{lane}.native_outcomes")):
        outcome = _keys(raw, ["terminal", "cleaned"], f"{lane}.native_outcomes[{index}]")
        cleanup += not _boolean(outcome["cleaned"], f"{lane}.native_outcomes[{index}].cleaned")
    return {
        "turn_service_samples_us": [
            _number(item, f"{lane}.turn_service_us")
            for item in _list(value["turn_service_us"], f"{lane}.turn_service_us")
        ],
        "ffi_output_mismatches": mismatches,
        "ffi_latency_regression_samples_percent": regressions,
        "unclean_native_outcomes": cleanup,
    }


def memory_governor(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    lane = "residency-and-memory-governor"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        [
            "governor_decisions",
            "lease_checks",
            "reservations",
            "pending_reclaim",
            "process_samples_bytes",
            "reclaim_us",
        ],
        lane,
    )
    unsafe = 0
    mode_order_violations = 0
    for index, raw in enumerate(_list(value["governor_decisions"], f"{lane}.governor_decisions")):
        decision = _keys(
            raw,
            [
                "resource_safe",
                "admitted",
                "observed_resource_mode",
                "safety_filter_sequence",
                "schedule_sequence",
            ],
            f"{lane}.governor_decisions[{index}]",
        )
        unsafe += _boolean(decision["admitted"], "admitted") and not _boolean(
            decision["resource_safe"], "resource_safe"
        )
        safety_sequence = _number(decision["safety_filter_sequence"], "safety_filter_sequence")
        schedule_sequence = _number(decision["schedule_sequence"], "schedule_sequence")
        mode_order_violations += (
            decision["observed_resource_mode"] != parameters.get("resource_mode")
            or safety_sequence >= schedule_sequence
        )
    lease_violations = 0
    lease_checks = _list(value["lease_checks"], f"{lane}.lease_checks")
    if not lease_checks:
        raise ContractError(f"{lane}.lease_checks must not be empty")
    for index, raw in enumerate(lease_checks):
        check = _keys(
            raw,
            ["busy", "loading", "lease_count", "selected_for_unload"],
            f"{lane}.lease_checks[{index}]",
        )
        lease_count = _number(check["lease_count"], "lease_count")
        if lease_count < 0 or not lease_count.is_integer():
            raise ContractError(f"{lane}.lease_checks[{index}].lease_count is invalid")
        protected = (
            _boolean(check["busy"], "busy")
            or _boolean(check["loading"], "loading")
            or lease_count > 0
        )
        lease_violations += protected and _boolean(
            check["selected_for_unload"], "selected_for_unload"
        )
    errors = []
    for index, raw in enumerate(_list(value["reservations"], f"{lane}.reservations")):
        reservation = _keys(
            raw, ["reserved", "allocated", "pending_reclaim", "released"],
            f"{lane}.reservations[{index}]",
        )
        reserved = _number(reservation["reserved"], "reserved")
        accounted = sum(
            _number(reservation[key], key) for key in ("allocated", "pending_reclaim", "released")
        )
        errors.append(abs(reserved - accounted))
    premature_releases = 0
    pending_items = _list(value["pending_reclaim"], f"{lane}.pending_reclaim")
    if not pending_items:
        raise ContractError(f"{lane}.pending_reclaim must not be empty")
    for index, raw in enumerate(pending_items):
        pending = _keys(
            raw,
            ["process_reclaimed", "backend_reclaimed", "reservation_held"],
            f"{lane}.pending_reclaim[{index}]",
        )
        converged = _boolean(pending["process_reclaimed"], "process_reclaimed") and _boolean(
            pending["backend_reclaimed"], "backend_reclaimed"
        )
        premature_releases += not converged and not _boolean(
            pending["reservation_held"], "reservation_held"
        )
    return {
        "unsafe_admissions": unsafe,
        "lease_unsafe_unloads": lease_violations,
        "resource_mode_order_violations": mode_order_violations,
        "reservation_accounting_error_bytes": max(errors, default=0),
        "premature_reclaim_releases": premature_releases,
        "observed_peak_memory_bytes": max(
            _number(item, f"{lane}.process_samples_bytes")
            for item in _list(value["process_samples_bytes"], f"{lane}.process_samples_bytes")
        ),
        "reclaim_convergence_samples_us": [
            _number(item, f"{lane}.reclaim_us")
            for item in _list(value["reclaim_us"], f"{lane}.reclaim_us")
        ],
    }


def cross_model_serving(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    lane = "cross-model-serving"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        ["progress_us", "timing_us", "receipts", "latency_samples", "throughput", "outputs"],
        lane,
    )
    progress_samples = [
        _number(item, f"{lane}.progress_us[{index}]")
        for index, item in enumerate(_list(value["progress_us"], f"{lane}.progress_us"))
    ]
    timing_samples = [
        _number(item, f"{lane}.timing_us[{index}]")
        for index, item in enumerate(_list(value["timing_us"], f"{lane}.timing_us"))
    ]
    if (
        not progress_samples
        or not timing_samples
        or any(item <= 0 for item in [*progress_samples, *timing_samples])
    ):
        raise ContractError(f"{lane} progress and timing samples must be non-empty and positive")
    ledgers: Dict[str, float] = {}
    observed_weights: Dict[str, float] = {}
    for raw in _list(value["receipts"], f"{lane}.receipts"):
        receipt = _keys(raw, ["model", "engine_service_us", "weight"], f"{lane}.receipts")
        weight = _number(receipt["weight"], "weight")
        if weight <= 0:
            raise ContractError(f"{lane} receipt weight must be positive")
        model = str(receipt["model"])
        if model not in {"alpha", "beta"}:
            raise ContractError(f"{lane} receipt model must be alpha or beta")
        if model in observed_weights and observed_weights[model] != weight:
            raise ContractError(f"{lane} receipt weight changed within one case")
        observed_weights[model] = weight
        ledgers[model] = ledgers.get(model, 0.0) + _number(
            receipt["engine_service_us"], "engine_service_us"
        ) / weight
    if set(ledgers) != {"alpha", "beta"}:
        raise ContractError(f"{lane} requires receipts for both models")
    expected_beta_weight = 3.0 if parameters.get("model_weights") == "1_to_3" else 1.0
    weights_match = observed_weights == {"alpha": 1.0, "beta": expected_beta_weight}
    mean = sum(ledgers.values()) / len(ledgers)
    service_error = 0.0 if mean == 0 else (max(ledgers.values()) - min(ledgers.values())) / mean * 100.0
    if not weights_match:
        service_error = max(service_error, 100.0)
    ttft_samples: List[float] = []
    tpot_samples: List[float] = []
    for index, raw in enumerate(_list(value["latency_samples"], f"{lane}.latency_samples")):
        sample = _keys(
            raw,
            ["ttft_us", "tpot_us"],
            f"{lane}.latency_samples[{index}]",
        )
        ttft = _number(sample["ttft_us"], f"{lane}.latency_samples[{index}].ttft_us")
        tpot = _number(sample["tpot_us"], f"{lane}.latency_samples[{index}].tpot_us")
        if ttft <= 0 or tpot <= 0:
            raise ContractError(f"{lane} latency samples must be positive")
        ttft_samples.append(ttft)
        tpot_samples.append(tpot)
    if not ttft_samples:
        raise ContractError(f"{lane} requires latency samples")
    throughput = _keys(
        value["throughput"], ["tokens", "seconds"], f"{lane}.throughput"
    )
    tokens = _number(throughput["tokens"], f"{lane}.throughput.tokens")
    seconds = _number(throughput["seconds"], f"{lane}.throughput.seconds")
    if tokens < 0 or seconds <= 0:
        raise ContractError(f"{lane} throughput requires tokens >= 0 and seconds > 0")
    output_mismatches = 0
    for raw in _list(value["outputs"], f"{lane}.outputs"):
        pair = _keys(raw, ["expected", "observed"], f"{lane}.outputs")
        output_mismatches += pair["expected"] != pair["observed"]
    return {
        "progress_samples_us": progress_samples,
        "timing_samples_us": timing_samples,
        "weighted_service_error_percent": service_error,
        "ttft_samples_us": ttft_samples,
        "tpot_samples_us": tpot_samples,
        "throughput_work": {"operations": tokens, "seconds": seconds},
        "serving_output_mismatches": output_mismatches,
    }


def observability(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "observability-qualification"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        [
            "turn_ids",
            "command_buffers",
            "instruments_pairs",
            "telemetry",
            "instruments_capture_present",
        ],
        lane,
    )
    turns = set(_list(value["turn_ids"], f"{lane}.turn_ids"))
    buffers = _list(value["command_buffers"], f"{lane}.command_buffers")
    timestamped = 0
    attribution_errors = 0
    for raw in buffers:
        item = _keys(raw, ["turn_id", "gpu_start_ns", "gpu_end_ns", "status"], f"{lane}.command_buffers")
        start = _number(item["gpu_start_ns"], "gpu_start_ns")
        end = _number(item["gpu_end_ns"], "gpu_end_ns")
        timestamped += start > 0 and end >= start
        attribution_errors += item["turn_id"] not in turns or item["status"] not in {"completed", "error"}
    coverage = 100.0 if not buffers else timestamped / len(buffers) * 100.0
    errors = []
    for raw in _list(value["instruments_pairs"], f"{lane}.instruments_pairs"):
        pair = _keys(raw, ["measured_us", "instruments_us"], f"{lane}.instruments_pairs")
        instruments = _number(pair["instruments_us"], "instruments_us")
        if instruments <= 0:
            raise ContractError(f"{lane} Instruments duration must be positive")
        errors.append(abs(_number(pair["measured_us"], "measured_us") - instruments) / instruments * 100.0)
    telemetry = _keys(
        value["telemetry"],
        ["off_throughput", "on_throughput", "off_tpot_p95_us", "on_tpot_p95_us"],
        f"{lane}.telemetry",
    )
    off_throughput = _number(telemetry["off_throughput"], "off_throughput")
    off_tpot = _number(telemetry["off_tpot_p95_us"], "off_tpot_p95_us")
    if off_throughput <= 0 or off_tpot <= 0 or not errors:
        raise ContractError(f"{lane} calibration and off baselines must be positive")
    return {
        "instruments_capture_missing": int(
            not _boolean(
                value["instruments_capture_present"],
                f"{lane}.instruments_capture_present",
            )
        ),
        "timestamp_coverage_percent": coverage,
        "attribution_errors": attribution_errors,
        "instruments_relative_error_samples_percent": errors,
        "instruments_relative_error_p95_samples_percent": errors,
        "telemetry_throughput_regression_percent": max(
            0.0,
            (off_throughput - _number(telemetry["on_throughput"], "on_throughput"))
            / off_throughput
            * 100.0,
        ),
        "telemetry_tpot_p95_regression_percent": max(
            0.0,
            (_number(telemetry["on_tpot_p95_us"], "on_tpot_p95_us") - off_tpot)
            / off_tpot
            * 100.0,
        ),
    }


def persistence(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "persistence-and-recovery"
    _empty_close(close, lane)
    value = _keys(_record(evidence, lane), ["snapshots", "invalid_restore_attempts", "authorities", "effects"], lane)
    snapshot_mismatches = sum(
        _keys(item, ["expected", "observed"], f"{lane}.snapshots")["expected"]
        != _keys(item, ["expected", "observed"], f"{lane}.snapshots")["observed"]
        for item in _list(value["snapshots"], f"{lane}.snapshots")
    )
    invalid_restores = 0
    for item in _list(value["invalid_restore_attempts"], f"{lane}.invalid_restore_attempts"):
        attempt = _keys(item, ["valid", "restored"], f"{lane}.invalid_restore_attempts")
        invalid_restores += not _boolean(attempt["valid"], "valid") and _boolean(attempt["restored"], "restored")
    authority_mismatches = sum(
        _keys(item, ["expected", "observed"], f"{lane}.authorities")["expected"]
        != _keys(item, ["expected", "observed"], f"{lane}.authorities")["observed"]
        for item in _list(value["authorities"], f"{lane}.authorities")
    )
    replayed = sum(
        max(0, int(_number(_keys(item, ["id", "executions"], f"{lane}.effects")["executions"], "executions")) - 1)
        for item in _list(value["effects"], f"{lane}.effects")
    )
    return {
        "snapshot_roundtrip_mismatches": snapshot_mismatches,
        "invalid_snapshot_restores": invalid_restores,
        "authoritative_state_divergences": authority_mismatches,
        "replayed_external_effects": replayed,
    }


def owner_lifecycle(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    del parameters
    lane = "protocol-and-owner-lifecycle"
    _empty_close(close, lane)
    value = _keys(_record(evidence, lane), ["daemon", "client_transport"], lane)
    daemon = _keys(
        value["daemon"],
        ["owners", "backend_calls_before_initialization", "successful_receipts_after_daemon_loss"],
        f"{lane}.daemon",
    )
    transport = _keys(
        value["client_transport"],
        ["frame_bytes", "latency_us"],
        f"{lane}.client_transport",
    )
    frame_bytes = [
        _number(item, f"{lane}.client_transport.frame_bytes[{index}]")
        for index, item in enumerate(
            _list(transport["frame_bytes"], f"{lane}.client_transport.frame_bytes")
        )
    ]
    latency_us = [
        _number(item, f"{lane}.client_transport.latency_us[{index}]")
        for index, item in enumerate(
            _list(transport["latency_us"], f"{lane}.client_transport.latency_us")
        )
    ]
    if not latency_us or any(item <= 0 for item in latency_us):
        raise ContractError(
            f"{lane} client transport latency samples must be non-empty and positive"
        )
    if any(item < 0 for item in frame_bytes):
        raise ContractError(
            f"{lane} client transport frame bytes must be non-negative"
        )
    backend_before = _number(
        daemon["backend_calls_before_initialization"],
        f"{lane}.daemon.backend_calls_before_initialization",
    )
    receipts_after_loss = _number(
        daemon["successful_receipts_after_daemon_loss"],
        f"{lane}.daemon.successful_receipts_after_daemon_loss",
    )
    if backend_before < 0 or receipts_after_loss < 0:
        raise ContractError(
            f"{lane} daemon counts must be non-negative"
        )
    return {
        "simultaneous_device_owner_count": len(
            set(_list(value["daemon"]["owners"], f"{lane}.daemon.owners"))
        ),
        "backend_calls_before_initialization_count": backend_before,
        "successful_receipt_after_daemon_loss_count": receipts_after_loss,
        "client_transport_max_frame_bytes": max(frame_bytes, default=0),
        "client_transport_latency_samples_us": latency_us,
    }


def certification(
    parameters: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], close: Mapping[str, Any]
) -> Mapping[str, Any]:
    lane = "certification-envelopes"
    _empty_close(close, lane)
    value = _keys(
        _record(evidence, lane),
        ["admissions", "scope_uses", "quarantine", "recertification"],
        lane,
    )
    expected_applicable = (
        parameters.get("changed_identity") == "none"
        and parameters.get("record_state") == "applicable"
    )
    uncertified = 0
    applicability_mismatches = 0
    for item in _list(value["admissions"], f"{lane}.admissions"):
        admission = _keys(
            item,
            ["observed_record_applicable", "shared_turn_started"],
            f"{lane}.admissions",
        )
        observed_applicable = _boolean(
            admission["observed_record_applicable"], "observed_record_applicable"
        )
        shared_started = _boolean(
            admission["shared_turn_started"], "shared_turn_started"
        )
        applicability_mismatches += (
            observed_applicable != expected_applicable
            or shared_started != expected_applicable
        )
        uncertified += not expected_applicable and shared_started
    scope_leaks = sum(
        _keys(item, ["certified", "used"], f"{lane}.scope_uses")["certified"]
        != _keys(item, ["certified", "used"], f"{lane}.scope_uses")["used"]
        for item in _list(value["scope_uses"], f"{lane}.scope_uses")
    )
    quarantine = _keys(
        value["quarantine"], ["bound_violation_sequence", "turn_start_sequences"],
        f"{lane}.quarantine",
    )
    violation = _number(quarantine["bound_violation_sequence"], "bound_violation_sequence")
    delayed = sum(
        _number(item, "turn_start_sequence") > violation
        for item in _list(quarantine["turn_start_sequences"], f"{lane}.turn_start_sequences")
    )
    recertification = _keys(
        value["recertification"],
        ["required", "completed", "new_record_applied", "shared_turn_started_after"],
        f"{lane}.recertification",
    )
    expected_recertification = not expected_applicable
    observed_recertification = {
        key: _boolean(recertification[key], f"{lane}.recertification.{key}")
        for key in recertification
    }
    recertification_failures = int(
        observed_recertification["required"] != expected_recertification
        or observed_recertification["completed"] != expected_recertification
        or observed_recertification["new_record_applied"] != expected_recertification
        or observed_recertification["shared_turn_started_after"]
        != expected_recertification
    )
    return {
        "certification_applicability_mismatches": applicability_mismatches,
        "uncertified_shared_turns": uncertified,
        "certification_scope_leaks": scope_leaks,
        "turns_after_bound_violation": delayed,
        "explicit_recertification_failures": recertification_failures,
    }


ANALYZERS: Mapping[
    str,
    Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]],
] = {
    "core-event-replay": core_event_replay,
    "scheduler-performance": scheduler_performance,
    "request-serving-lifecycle": request_serving,
    "mlx-native-correctness": mlx_fixture,
    "bounded-turn-and-ffi": bounded_turn,
    "residency-and-memory-governor": memory_governor,
    "cross-model-serving": cross_model_serving,
    "observability-qualification": observability,
    "persistence-and-recovery": persistence,
    "protocol-and-owner-lifecycle": owner_lifecycle,
    "certification-envelopes": certification,
}


def analyze_lane_evidence(
    lane_id: str,
    parameters: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    close: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        analyzer = ANALYZERS[lane_id]
    except KeyError as error:
        raise ContractError(f"no normalized evidence oracle for lane {lane_id!r}") from error
    return analyzer(parameters, evidence, close)
