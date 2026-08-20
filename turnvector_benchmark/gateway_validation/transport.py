from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..core import ContractError
from .common import read_jsonl
from .contract import (
    GatewayValidationContract,
    _array,
    _identifier,
    _integer,
    _object,
    _strict_keys,
)


TRIAL_FIELDS = (
    "case_id",
    "repetition",
    "stages",
    "request_variable_ns",
    "cpu_ns",
    "context_switches",
    "frame_payload_bytes",
    "fd_peak",
    "connection_count",
    "error_count",
    "first_response_ns",
)


def _nearest_rank(values: Sequence[int], quantile: float) -> int:
    if not values:
        raise ContractError("cannot reduce an empty Gateway sample set")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _distribution(values: Sequence[int]) -> Mapping[str, int]:
    return {
        "min": min(values),
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "p99": _nearest_rank(values, 0.99),
        "max": max(values),
    }


def _fraction(value: Fraction) -> Mapping[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _empty_samples(
    contract: GatewayValidationContract,
) -> Dict[str, List[int]]:
    return {
        "setup_ns": [],
        "total_ns": [],
        "cpu_ns": [],
        "context_switches": [],
        "wire_bytes": [],
        "fd_peak": [],
        "first_response_ns": [],
        **{stage_id: [] for stage_id in contract.stage_ids},
    }


def validate_transport(
    contract: GatewayValidationContract, path: Path
) -> Tuple[List[Mapping[str, Any]], List[str], int]:
    rows = read_jsonl(path, "Gateway transport trials")
    plan = contract.transport_case_plan()
    repetitions = contract.transport_protocol.measured_repetitions
    expected_count = len(plan) * repetitions
    if len(rows) != expected_count:
        raise ContractError(f"transport trials must contain exactly {expected_count} rows")
    plan_by_id = {case.case_id: case for case in plan}
    schedule = []
    for repetition in range(repetitions):
        ordered = plan if repetition % 2 == 0 else tuple(reversed(plan))
        schedule.extend((case.case_id, repetition) for case in ordered)
    samples = {case.case_id: _empty_samples(contract) for case in plan}
    seen: set[Tuple[str, int]] = set()
    reasons: List[str] = []
    for index, raw in enumerate(rows):
        where = f"transport_trials[{index}]"
        row = _object(raw, where)
        _strict_keys(row, TRIAL_FIELDS, [], where)
        case_id = _identifier(row["case_id"], f"{where}.case_id")
        planned = plan_by_id.get(case_id)
        if planned is None:
            raise ContractError(f"{where}.case_id is not in the transport CasePlan")
        repetition = _integer(row["repetition"], f"{where}.repetition")
        if repetition >= repetitions or (case_id, repetition) in seen:
            raise ContractError(f"{where} has an invalid or duplicate repetition")
        if (case_id, repetition) != schedule[index]:
            raise ContractError(f"{where} does not preserve the balanced CasePlan order")
        seen.add((case_id, repetition))
        stages = _object(row["stages"], f"{where}.stages")
        _strict_keys(stages, contract.stage_ids, [], f"{where}.stages")
        parsed_stages = {
            stage_id: _integer(stages[stage_id], f"{where}.stages.{stage_id}")
            for stage_id in contract.stage_ids
        }
        variable = _integer(row["request_variable_ns"], f"{where}.request_variable_ns")
        cpu = _integer(row["cpu_ns"], f"{where}.cpu_ns")
        switches = _integer(row["context_switches"], f"{where}.context_switches")
        payloads = [
            _integer(value, f"{where}.frame_payload_bytes[{payload_index}]")
            for payload_index, value in enumerate(
                _array(row["frame_payload_bytes"], f"{where}.frame_payload_bytes")
            )
        ]
        fd_peak = _integer(row["fd_peak"], f"{where}.fd_peak")
        connection_count = _integer(row["connection_count"], f"{where}.connection_count")
        error_count = _integer(row["error_count"], f"{where}.error_count")
        first_response = _integer(row["first_response_ns"], f"{where}.first_response_ns")
        if connection_count != 1:
            reasons.append(f"{case_id}:connection_count_not_one")
        if error_count != 0:
            reasons.append(f"{case_id}:transport_error")
        if fd_peak == 0:
            reasons.append(f"{case_id}:fd_peak_missing")
        if planned.parameters["probe_path"] == "production_data_plane" and first_response == 0:
            reasons.append(f"{case_id}:first_response_missing")
        setup = sum(parsed_stages.values())
        target = samples[case_id]
        target["setup_ns"].append(setup)
        target["total_ns"].append(setup + variable)
        target["cpu_ns"].append(cpu)
        target["context_switches"].append(switches)
        target["wire_bytes"].append(sum(4 + payload for payload in payloads))
        target["fd_peak"].append(fd_peak)
        target["first_response_ns"].append(first_response)
        for stage_id, observed in parsed_stages.items():
            target[stage_id].append(observed)
    if len(seen) != expected_count:
        raise ContractError("transport trials do not cover every case and repetition")

    reports: List[Mapping[str, Any]] = []
    for planned in plan:
        case_samples = samples[planned.case_id]
        setup_p99 = _nearest_rank(case_samples["setup_ns"], 0.99)
        reports.append(
            {
                "case_id": planned.case_id,
                "parameters": dict(planned.parameters),
                "trial_count": repetitions,
                "setup_ns": _distribution(case_samples["setup_ns"]),
                "total_ns": _distribution(case_samples["total_ns"]),
                "cpu_ns": _distribution(case_samples["cpu_ns"]),
                "context_switches": _distribution(case_samples["context_switches"]),
                "wire_bytes": _distribution(case_samples["wire_bytes"]),
                "fd_peak": _distribution(case_samples["fd_peak"]),
                "first_response_ns": _distribution(case_samples["first_response_ns"]),
                "stages": {
                    stage_id: _distribution(case_samples[stage_id])
                    for stage_id in contract.stage_ids
                },
                "predicted_perfect_reuse_upper_bounds": [
                    {
                        "reuse_factor": factor,
                        "setup_p99_savings_ns": _fraction(
                            Fraction(setup_p99 * (factor - 1), factor)
                        ),
                    }
                    for factor in contract.reuse_factors
                ],
            }
        )
    return reports, sorted(set(reasons)), expected_count
