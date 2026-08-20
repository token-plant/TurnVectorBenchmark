from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError
from .common import read_jsonl
from .contract import (
    GatewayValidationContract,
    LifecycleCase,
    _array,
    _identifier,
    _integer,
    _object,
    _strict_keys,
)


def _event_times(
    events: Sequence[Mapping[str, Any]], kind: str, role: str = "a"
) -> List[int]:
    return [
        int(event["at_ns"])
        for event in events
        if event["kind"] == kind and event["request_role"] == role
    ]


def _first_time(
    events: Sequence[Mapping[str, Any]], kind: str, role: str = "a"
) -> Optional[int]:
    values = _event_times(events, kind, role)
    return values[0] if values else None


def _ordered(values: Iterable[Optional[int]]) -> bool:
    present = list(values)
    return all(value is not None for value in present) and all(
        int(left) <= int(right) for left, right in zip(present, present[1:])
    )


def _fraction(value: Fraction) -> Mapping[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _require_once(
    events: Sequence[Mapping[str, Any]], kinds: Iterable[str], violations: List[str]
) -> None:
    for kind in kinds:
        if len(_event_times(events, kind)) != 1:
            violations.append(f"event_count:{kind}")


def _validate_outcome(
    planned: LifecycleCase,
    events: Sequence[Mapping[str, Any]],
    times: Mapping[str, Optional[int]],
    limits: Mapping[str, int],
) -> List[str]:
    violations: List[str] = []
    if planned.expected_outcome == "complete":
        _require_once(events, ["http_last_byte_committed"], violations)
        if not _ordered(
            [
                times["terminal_observed"],
                times["http_last_byte_committed"],
                times["response_closed"],
            ]
        ):
            violations.append("complete_response_order")
    elif planned.expected_outcome == "bounded_close":
        _require_once(events, ["backpressure_entered", "deadline_expired"], violations)
        if not _ordered(
            [
                times["backpressure_entered"],
                times["deadline_expired"],
                times["response_closed"],
            ]
        ):
            violations.append("bounded_close_order")
        if _duration(times, "backpressure_entered", "response_closed") > limits[
            "http_write_total_ns"
        ]:
            violations.append("bounded_close_deadline")
        if _duration(times, "backpressure_entered", "deadline_expired") > limits[
            "http_write_no_progress_ns"
        ]:
            violations.append("write_no_progress_deadline")
    elif planned.expected_outcome == "bounded_cancel":
        _require_once(
            events,
            ["backpressure_entered", "deadline_expired", "cancel_ordered"],
            violations,
        )
        if not _ordered(
            [
                times["backpressure_entered"],
                times["deadline_expired"],
                times["cancel_ordered"],
                times["terminal_observed"],
                times["request_state_release_accepted"],
                times["response_closed"],
            ]
        ):
            violations.append("bounded_cancel_order")
        maximum = limits["http_write_total_ns"] + limits["cancellation_completion_ns"]
        if _duration(times, "backpressure_entered", "response_closed") > maximum:
            violations.append("bounded_cancel_deadline")
        if _duration(times, "backpressure_entered", "deadline_expired") > limits[
            "http_write_no_progress_ns"
        ]:
            violations.append("write_no_progress_deadline")
    elif planned.expected_outcome == "disconnect_cancel":
        _require_once(events, ["client_disconnected", "cancel_ordered"], violations)
        if not _ordered(
            [
                times["client_disconnected"],
                times["cancel_ordered"],
                times["terminal_observed"],
                times["request_state_release_accepted"],
                times["response_closed"],
            ]
        ):
            violations.append("disconnect_cancel_order")
        if _duration(
            times, "client_disconnected", "request_state_release_accepted"
        ) > limits["cancellation_completion_ns"]:
            violations.append("disconnect_cancel_deadline")
    else:
        raise ContractError(f"unsupported expected lifecycle outcome {planned.expected_outcome!r}")
    return violations


def _duration(times: Mapping[str, Optional[int]], start: str, end: str) -> int:
    start_value = times[start]
    end_value = times[end]
    if start_value is None or end_value is None or end_value < start_value:
        return 0
    return end_value - start_value


def _validate_case(
    raw: Any,
    planned: LifecycleCase,
    contract: GatewayValidationContract,
    limits: Mapping[str, int],
    index: int,
) -> Tuple[Mapping[str, Any], List[str]]:
    where = f"lifecycle_trace[{index}]"
    obj = _object(raw, where)
    _strict_keys(obj, ["case_id", "events", "counters", "queue"], [], where)
    if obj["case_id"] != planned.case_id:
        raise ContractError(f"{where}.case_id does not preserve the lifecycle CasePlan")
    events: List[Mapping[str, Any]] = []
    prior_time = -1
    for sequence, raw_event in enumerate(_array(obj["events"], f"{where}.events")):
        event_where = f"{where}.events[{sequence}]"
        event = _object(raw_event, event_where)
        _strict_keys(event, ["sequence", "request_role", "kind", "at_ns"], [], event_where)
        if event["sequence"] != sequence:
            raise ContractError(f"{event_where}.sequence must preserve event order")
        role = _identifier(event["request_role"], f"{event_where}.request_role")
        if role not in {"a", "b"}:
            raise ContractError(f"{event_where}.request_role must be a or b")
        kind = _identifier(event["kind"], f"{event_where}.kind")
        if kind not in contract.event_kinds:
            raise ContractError(f"{event_where}.kind is not allowed by the contract")
        at_ns = _integer(event["at_ns"], f"{event_where}.at_ns")
        if at_ns < prior_time:
            raise ContractError(f"{event_where}.at_ns must be monotonic")
        prior_time = at_ns
        events.append(
            {"sequence": sequence, "request_role": role, "kind": kind, "at_ns": at_ns}
        )

    counters = _object(obj["counters"], f"{where}.counters")
    _strict_keys(counters, contract.counter_ids, [], f"{where}.counters")
    parsed_counters = {
        counter_id: _integer(counters[counter_id], f"{where}.counters.{counter_id}")
        for counter_id in contract.counter_ids
    }
    queue = _object(obj["queue"], f"{where}.queue")
    queue_fields = [
        "capacity_bytes",
        "initial_occupancy_bytes",
        "producer_bytes_per_second",
        "consumer_bytes_per_second",
        "peak_occupancy_bytes",
    ]
    _strict_keys(queue, queue_fields, [], f"{where}.queue")
    parsed_queue = {
        field: _integer(queue[field], f"{where}.queue.{field}") for field in queue_fields
    }
    if parsed_queue["capacity_bytes"] <= 0:
        raise ContractError(f"{where}.queue.capacity_bytes must be positive")

    violations = [
        f"counter_nonzero:{counter_id}"
        for counter_id, count in parsed_counters.items()
        if count != 0
    ]
    if parsed_queue["initial_occupancy_bytes"] > parsed_queue["capacity_bytes"]:
        violations.append("initial_queue_occupancy_exceeds_capacity")
    if parsed_queue["peak_occupancy_bytes"] > parsed_queue["capacity_bytes"]:
        violations.append("peak_queue_occupancy_exceeds_capacity")

    required_once = (
        "exchange_reserved",
        "request_accepted",
        "backend_ownership_acquired",
        "output_production_started",
        "terminal_observed",
        "request_state_release_accepted",
        "response_closed",
    )
    _require_once(events, required_once, violations)
    times = {kind: _first_time(events, kind) for kind in contract.event_kinds}
    if not _ordered(times[kind] for kind in required_once):
        violations.append("base_lifecycle_order")
    violations.extend(_validate_outcome(planned, events, times, limits))

    release = times["request_state_release_accepted"]
    close = times["response_closed"]
    if planned.requires_decoupling and (release is None or close is None or release >= close):
        violations.append("backend_response_lifetime_not_decoupled")
    if planned.requires_peer_progress:
        peers = _event_times(events, "peer_request_progress", "b")
        if release is None or close is None or not any(release < peer < close for peer in peers):
            violations.append("peer_progress_missing_from_response_tail")

    acquired = times["backend_ownership_acquired"]
    exchange = times["exchange_reserved"]
    metrics: dict[str, Any] = {
        "backend_ns": release - acquired if release is not None and acquired is not None else None,
        "tail_ns": max(0, close - release) if close is not None and release is not None else None,
        "response_ns": close - exchange if close is not None and exchange is not None else None,
        "peak_queue_bytes": parsed_queue["peak_occupancy_bytes"],
    }
    producer = parsed_queue["producer_bytes_per_second"]
    consumer = parsed_queue["consumer_bytes_per_second"]
    if producer > consumer:
        remaining = parsed_queue["capacity_bytes"] - parsed_queue["initial_occupancy_bytes"]
        metrics["predicted_fill_ns"] = _fraction(
            Fraction(max(0, remaining) * 1_000_000_000, producer - consumer)
        )
    else:
        metrics["predicted_fill_ns"] = None
    production = times["output_production_started"]
    backpressure = times["backpressure_entered"]
    metrics["observed_fill_ns"] = (
        backpressure - production
        if backpressure is not None and production is not None
        else None
    )
    report = {
        "case_id": planned.case_id,
        "status": "passed" if not violations else "failed",
        "violations": violations,
        "metrics": metrics,
    }
    return report, [f"{planned.case_id}:{violation}" for violation in violations]


def validate_lifecycle(
    contract: GatewayValidationContract,
    path: Path,
    limits: Mapping[str, int],
) -> Tuple[List[Mapping[str, Any]], List[str]]:
    rows = read_jsonl(path, "Gateway lifecycle trace")
    if len(rows) != len(contract.lifecycle_cases):
        raise ContractError("lifecycle trace must contain the exact lifecycle CasePlan")
    reports: List[Mapping[str, Any]] = []
    reasons: List[str] = []
    for index, (raw, planned) in enumerate(zip(rows, contract.lifecycle_cases)):
        report, case_reasons = _validate_case(raw, planned, contract, limits, index)
        reports.append(report)
        reasons.extend(case_reasons)
    return reports, reasons
