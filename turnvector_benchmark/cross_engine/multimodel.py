from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from ..core import ContractError


@dataclass(frozen=True)
class ModelWorkload:
    model_id: str
    model_artifact_identity: str
    arrival_trace_ms: Tuple[float, ...]
    output_work_identity: str
    service_weight: float

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_artifact_identity or not self.output_work_identity:
            raise ContractError("multi-model workload identities must be non-empty")
        if (
            isinstance(self.service_weight, bool)
            or not isinstance(self.service_weight, (int, float))
            or not math.isfinite(float(self.service_weight))
            or self.service_weight <= 0
        ):
            raise ContractError("multi-model service weight must be finite and positive")
        if not self.arrival_trace_ms:
            raise ContractError("each configured model must have a non-empty arrival trace")
        prior = -1.0
        for value in self.arrival_trace_ms:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < prior
            ):
                raise ContractError("arrival trace must be finite, non-negative, and ordered")
            prior = float(value)


@dataclass(frozen=True)
class MultiModelCell:
    deployment_topology: str
    workloads: Tuple[ModelWorkload, ...]
    duration_ms: float
    concurrency: int
    memory_budget_bytes: int
    admission_policy: str
    scheduler_cost_boundary_identity: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.deployment_topology or not self.admission_policy:
            raise ContractError("multi-model topology and admission policy must be named")
        if not self.workloads:
            raise ContractError("multi-model cell must configure at least one model")
        model_ids = [workload.model_id for workload in self.workloads]
        if len(model_ids) != len(set(model_ids)):
            raise ContractError("multi-model resident model IDs must be unique")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, (int, float))
            or not math.isfinite(float(self.duration_ms))
            or self.duration_ms <= 0
        ):
            raise ContractError("multi-model duration must be finite and positive")
        if isinstance(self.concurrency, bool) or not isinstance(self.concurrency, int) or self.concurrency < 1:
            raise ContractError("multi-model concurrency must be a positive integer")
        if (
            isinstance(self.memory_budget_bytes, bool)
            or not isinstance(self.memory_budget_bytes, int)
            or self.memory_budget_bytes < 1
        ):
            raise ContractError("multi-model memory budget must be a positive integer")
        if any(trace > self.duration_ms for row in self.workloads for trace in row.arrival_trace_ms):
            raise ContractError("arrival trace cannot extend beyond the cell duration")

    @property
    def resident_model_set(self) -> Tuple[str, ...]:
        return tuple(sorted(workload.model_id for workload in self.workloads))

    @property
    def row_identity(self) -> Tuple[object, ...]:
        workload_identity = tuple(
            (
                row.model_id,
                row.model_artifact_identity,
                row.arrival_trace_ms,
                row.output_work_identity,
                float(row.service_weight),
            )
            for row in self.workloads
        )
        return (
            self.deployment_topology,
            workload_identity,
            float(self.duration_ms),
            self.concurrency,
            self.memory_budget_bytes,
            self.admission_policy,
            self.scheduler_cost_boundary_identity,
        )


@dataclass(frozen=True)
class ModelObservation:
    model_id: str
    available: bool
    completion_timestamps_ms: Tuple[float, ...]
    output_tokens: int
    slo_good_completions: int

    def __post_init__(self) -> None:
        if not self.model_id:
            raise ContractError("multi-model observation model_id must be non-empty")
        if not isinstance(self.available, bool):
            raise ContractError("multi-model observation available must be a boolean")
        for field in ("output_tokens", "slo_good_completions"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError(f"multi-model {field} must be a non-negative integer")
        prior = -1.0
        for value in self.completion_timestamps_ms:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < prior
            ):
                raise ContractError("completion timestamps must be finite, non-negative, and ordered")
            prior = float(value)
        if self.slo_good_completions > len(self.completion_timestamps_ms):
            raise ContractError("SLO-good completions cannot exceed completions")


@dataclass(frozen=True)
class PerModelFairnessRow:
    model_id: str
    available: bool
    offered_load_requests_per_second: float
    completion_rate: float
    output_throughput_tokens_per_second: float
    slo_goodput_requests_per_second: float
    maximum_progress_gap_ms: float
    useful_service_share: float
    target_weighted_service_share: float
    weighted_service_error: float
    failure_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class MultiModelSummary:
    deployment_topology: str
    per_model_rows: Tuple[PerModelFairnessRow, ...]
    aggregate_output_throughput_tokens_per_second: float
    failed: bool


@dataclass(frozen=True)
class TopologyClaimBoundary:
    left_topology: str
    right_topology: str
    claim_kind: str
    reasons: Tuple[str, ...]

    @property
    def intrinsic_scheduler_claim_eligible(self) -> bool:
        return self.claim_kind == "intrinsic_scheduler"



def _maximum_progress_gap(timestamps: Sequence[float], duration_ms: float) -> float:
    points = (0.0, *(float(value) for value in timestamps), float(duration_ms))
    return max(right - left for left, right in zip(points, points[1:]))


def summarize_multi_model_cell(
    cell: MultiModelCell, observations: Sequence[ModelObservation]
) -> MultiModelSummary:
    """Emit one row per configured model, including unavailable/starved models."""
    by_model: Dict[str, ModelObservation] = {}
    configured = {workload.model_id for workload in cell.workloads}
    for observation in observations:
        if observation.model_id not in configured:
            raise ContractError(f"observation has an unconfigured model: {observation.model_id}")
        if observation.model_id in by_model:
            raise ContractError(f"duplicate observation for model: {observation.model_id}")
        if any(value > cell.duration_ms for value in observation.completion_timestamps_ms):
            raise ContractError("completion timestamp exceeds the cell duration")
        by_model[observation.model_id] = observation

    total_weight = sum(float(workload.service_weight) for workload in cell.workloads)
    total_output = sum(observation.output_tokens for observation in by_model.values())
    seconds = float(cell.duration_ms) / 1000.0
    rows = []
    for workload in cell.workloads:
        observation = by_model.get(
            workload.model_id,
            ModelObservation(workload.model_id, False, (), 0, 0),
        )
        completions = len(observation.completion_timestamps_ms)
        offered = len(workload.arrival_trace_ms)
        if completions > offered:
            raise ContractError("model completions cannot exceed offered requests")
        failures = []
        if not observation.available:
            failures.append("configured_model_unavailable")
        if completions == 0:
            failures.append("zero_completions")
        useful_share = observation.output_tokens / total_output if total_output else 0.0
        target_share = float(workload.service_weight) / total_weight
        rows.append(
            PerModelFairnessRow(
                model_id=workload.model_id,
                available=observation.available,
                offered_load_requests_per_second=offered / seconds,
                completion_rate=completions / offered,
                output_throughput_tokens_per_second=observation.output_tokens / seconds,
                slo_goodput_requests_per_second=observation.slo_good_completions / seconds,
                maximum_progress_gap_ms=_maximum_progress_gap(
                    observation.completion_timestamps_ms, float(cell.duration_ms)
                ),
                useful_service_share=useful_share,
                target_weighted_service_share=target_share,
                weighted_service_error=abs(useful_share - target_share),
                failure_reasons=tuple(failures),
            )
        )
    return MultiModelSummary(
        deployment_topology=cell.deployment_topology,
        per_model_rows=tuple(rows),
        aggregate_output_throughput_tokens_per_second=total_output / seconds,
        failed=any(row.failure_reasons for row in rows),
    )


def assess_topology_claim(left: MultiModelCell, right: MultiModelCell) -> TopologyClaimBoundary:
    """Keep topology comparisons descriptive unless every scheduler boundary is frozen/equal."""
    reasons = []
    if left.resident_model_set != right.resident_model_set:
        reasons.append("resident_model_set_mismatch")
    left_workloads = {row.model_id: row for row in left.workloads}
    right_workloads = {row.model_id: row for row in right.workloads}
    for model_id in sorted(set(left_workloads) & set(right_workloads)):
        left_row, right_row = left_workloads[model_id], right_workloads[model_id]
        if left_row.model_artifact_identity != right_row.model_artifact_identity:
            reasons.append("model_artifact_mismatch")
        if left_row.arrival_trace_ms != right_row.arrival_trace_ms:
            reasons.append("arrival_trace_mismatch")
        if left_row.output_work_identity != right_row.output_work_identity:
            reasons.append("output_work_mismatch")
        if left_row.service_weight != right_row.service_weight:
            reasons.append("service_weight_mismatch")
    for field in ("duration_ms", "concurrency", "memory_budget_bytes", "admission_policy"):
        if getattr(left, field) != getattr(right, field):
            reasons.append(f"{field}_mismatch")
    if (
        not left.scheduler_cost_boundary_identity
        or left.scheduler_cost_boundary_identity != right.scheduler_cost_boundary_identity
    ):
        reasons.append("scheduler_cost_boundary_not_frozen_or_mismatched")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return TopologyClaimBoundary(
        left_topology=left.deployment_topology,
        right_topology=right.deployment_topology,
        claim_kind=("intrinsic_scheduler" if not unique_reasons else "named_topology_only"),
        reasons=unique_reasons,
    )
