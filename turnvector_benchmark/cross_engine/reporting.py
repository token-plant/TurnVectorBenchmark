"""Orthogonal cross-engine status axes and report projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, Tuple

from turnvector_benchmark.core import ContractError


_CONTRACT = {"valid", "invalid", "interrupted"}
_CAPABILITY = {"supported", "capability_unsupported", "profile_incompatible", "not_applicable", "environment_unavailable"}
_EXECUTION = {"not_started", "completed", "partial", "infrastructure_failed"}
_EVIDENCE = {"not_evaluated", "publishable", "not_publishable"}
_PROMOTION = {"not_evaluated", "not_applicable", "passed", "failed"}
_COVERAGE = {"complete", "partial", "zero_common_cells"}


@dataclass(frozen=True)
class StatusAxes:
    contract_status: str
    capability_status: str
    execution_status: str
    evidence_status: str
    promotion_status: str
    coverage_status: str

    def __post_init__(self) -> None:
        for value, allowed, label in (
            (self.contract_status, _CONTRACT, "contract"),
            (self.capability_status, _CAPABILITY, "capability"),
            (self.execution_status, _EXECUTION, "execution"),
            (self.evidence_status, _EVIDENCE, "evidence"),
            (self.promotion_status, _PROMOTION, "promotion"),
            (self.coverage_status, _COVERAGE, "coverage"),
        ):
            if value not in allowed:
                raise ContractError("invalid %s status" % label)
        if self.contract_status != "valid" and (
            self.evidence_status != "not_evaluated" or self.promotion_status != "not_evaluated"
        ):
            raise ContractError("invalid/interrupted contracts cannot evaluate evidence or promotion")
        if self.capability_status != "supported" and self.execution_status == "completed":
            raise ContractError("unsupported capability cannot have completed execution")
        if self.evidence_status != "publishable" and self.promotion_status in {"passed", "failed"}:
            raise ContractError("promotion can be evaluated only for publishable evidence")

    def as_dict(self) -> Mapping[str, str]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Diagnostic:
    stage: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.stage or not self.code or not self.message:
            raise ContractError("diagnostic fields must be non-empty")
        if len(self.message.encode("utf-8")) > 1024:
            raise ContractError("diagnostic message exceeds 1024 UTF-8 bytes")

    def as_dict(self) -> Mapping[str, str]:
        return dict(self.__dict__)


_STAGE_ORDER = {
    "contract": 0,
    "capability": 1,
    "environment": 1,
    "lifecycle": 2,
    "execution": 2,
    "wire": 3,
    "output": 3,
    "metric": 4,
    "evidence_gate": 4,
    "promotion_gate": 5,
    "coverage": 6,
    "cleanup": 7,
}


def order_diagnostics(diagnostics: Sequence[Diagnostic]) -> Tuple[Diagnostic, ...]:
    values = tuple(diagnostics)
    if len(values) > 64:
        values = values[:64]
    try:
        return tuple(sorted(values, key=lambda item: (_STAGE_ORDER[item.stage], item.code, item.message)))
    except KeyError as error:
        raise ContractError("diagnostic has an unknown pipeline stage") from error


def primary_diagnostic(diagnostics: Sequence[Diagnostic]) -> Diagnostic | None:
    ordered = order_diagnostics(diagnostics)
    non_cleanup = [item for item in ordered if item.stage != "cleanup"]
    return non_cleanup[0] if non_cleanup else (ordered[0] if ordered else None)


def cross_engine_exit_code(axes: Sequence[StatusAxes], *, require_promotion: bool = False) -> int:
    values = tuple(axes)
    if not values:
        raise ContractError("cannot derive an exit code without statuses")
    if any(value.contract_status == "invalid" for value in values):
        return 2
    if any(value.contract_status == "interrupted" or value.execution_status == "infrastructure_failed" for value in values):
        return 6
    if any(value.evidence_status == "not_publishable" for value in values):
        return 3
    if any(value.capability_status in {"capability_unsupported", "profile_incompatible", "environment_unavailable"} for value in values):
        return 4
    if require_promotion and any(value.promotion_status == "failed" for value in values):
        return 5
    return 0


def build_report(
    run_id: str,
    axes: StatusAxes,
    diagnostics: Sequence[Diagnostic],
    *,
    coverage: Mapping[str, Any],
    metric_rows: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    if not isinstance(run_id, str) or not run_id:
        raise ContractError("run ID must be non-empty")
    ordered = order_diagnostics(diagnostics)
    primary = primary_diagnostic(ordered)
    return {
        "schema_version": "turnvector.benchmark.cross-engine-report.v1",
        "run_id": run_id,
        "statuses": axes.as_dict(),
        "primary_diagnostic": primary.as_dict() if primary else None,
        "diagnostics": [item.as_dict() for item in ordered],
        "coverage": dict(coverage),
        "metric_rows": [dict(row) for row in metric_rows],
        "qualification_claim": None,
    }
