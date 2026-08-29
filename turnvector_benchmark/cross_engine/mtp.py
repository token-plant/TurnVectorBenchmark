from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..core import ContractError
from .routes import RouteEvidence


MTP_MODES = frozenset({"direct", "configured_mtp"})


def _count(value: int, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{where} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class MTPCounts:
    drafted: int
    accepted: int
    rejected: int
    emitted: int
    terminal: int

    def __post_init__(self) -> None:
        for field in ("drafted", "accepted", "rejected", "emitted", "terminal"):
            _count(getattr(self, field), f"MTP count {field}")

    @property
    def accept_ratio(self) -> Optional[float]:
        return self.accepted / self.drafted if self.drafted else None

    def reconciles(self, fixed_output_tokens: int, *, direct: bool) -> bool:
        if not 0 <= self.accepted <= self.drafted:
            return False
        if self.accepted + self.rejected != self.drafted:
            return False
        if self.emitted != fixed_output_tokens or self.terminal != 1:
            return False
        return not direct or (self.drafted == self.accepted == self.rejected == 0)


@dataclass(frozen=True)
class MTPTrial:
    request_id: str
    mode: str
    target_id: str
    model_identity: str
    prompt_suite_identity: str
    sampling_identity: str
    host_session_id: str
    fixed_output_tokens: int
    output_token_hash: str
    route: RouteEvidence
    counts: MTPCounts
    elapsed_ms: Optional[float]
    completed: bool
    error: bool = False
    mtp_sidecar_identity: Optional[str] = None

    def __post_init__(self) -> None:
        if self.mode not in MTP_MODES:
            raise ContractError("MTP trial mode must be direct or configured_mtp")
        if not isinstance(self.completed, bool) or not isinstance(self.error, bool):
            raise ContractError("MTP trial completed and error fields must be booleans")
        if not self.request_id or self.route.request_id != self.request_id:
            raise ContractError("MTP trial must correlate to its route request_id")
        for field in (
            "target_id",
            "model_identity",
            "prompt_suite_identity",
            "sampling_identity",
            "host_session_id",
            "output_token_hash",
        ):
            if not getattr(self, field):
                raise ContractError(f"MTP trial {field} must be non-empty")
        _count(self.fixed_output_tokens, "fixed_output_tokens")
        if self.mode == "configured_mtp" and not self.mtp_sidecar_identity:
            raise ContractError("configured MTP trial must bind an MTP sidecar identity")
        if self.mode == "direct" and self.mtp_sidecar_identity is not None:
            raise ContractError("direct trial must not bind an MTP sidecar identity")
        if self.elapsed_ms is not None and (
            isinstance(self.elapsed_ms, bool)
            or not isinstance(self.elapsed_ms, (int, float))
            or not math.isfinite(float(self.elapsed_ms))
            or self.elapsed_ms <= 0
        ):
            raise ContractError("elapsed_ms must be finite and greater than zero")

    @property
    def count_reconciles(self) -> bool:
        return self.counts.reconciles(
            self.fixed_output_tokens, direct=self.mode == "direct"
        )

    @property
    def fallback(self) -> bool:
        normalized = self.route.normalized
        return self.mode == "configured_mtp" and (
            normalized.execution != "mtp" or normalized.fallback != "none"
        )


@dataclass(frozen=True)
class MTPAssessment:
    direct_trials: Tuple[MTPTrial, ...]
    configured_trials: Tuple[MTPTrial, ...]
    fallback_count: int
    error_count: int
    drafted_tokens: int
    accepted_draft_tokens: int
    mtp_accept_ratio: Optional[float]
    direct_mean_elapsed_ms: Optional[float]
    configured_mean_elapsed_ms: Optional[float]
    pure_mtp_speedup: Optional[float]
    pure_speedup_reasons: Tuple[str, ...]

    @property
    def pure_speedup_eligible(self) -> bool:
        return not self.pure_speedup_reasons



def _mean_elapsed(trials: Sequence[MTPTrial]) -> Optional[float]:
    if any(trial.elapsed_ms is None or not trial.completed or trial.error for trial in trials):
        return None
    return sum(float(trial.elapsed_ms) for trial in trials) / len(trials)


def assess_mtp_comparison(
    direct_trials: Sequence[MTPTrial],
    configured_trials: Sequence[MTPTrial],
    *,
    planned_configured_request_ids: Sequence[str],
    sampling_mode: str,
    reproducibly_coupled_sampling: bool = False,
) -> MTPAssessment:
    """Assess one same-target direct/MTP pair without dropping fallback attempts."""
    direct = tuple(direct_trials)
    configured = tuple(configured_trials)
    if not direct or len(direct) != len(configured):
        raise ContractError("MTP comparison requires equal non-empty direct and candidate trials")
    planned = tuple(planned_configured_request_ids)
    observed = tuple(trial.request_id for trial in configured)
    if len(planned) != len(set(planned)) or len(observed) != len(set(observed)):
        raise ContractError("MTP request IDs must be unique")
    if observed != planned:
        raise ContractError(
            "configured MTP trials must retain every planned request in frozen order"
        )
    if any(trial.mode != "direct" for trial in direct):
        raise ContractError("MTP denominator contains a non-direct trial")
    if any(trial.mode != "configured_mtp" for trial in configured):
        raise ContractError("MTP candidate contains a non-MTP configuration")

    reasons = []
    pair_fields = (
        "target_id",
        "model_identity",
        "prompt_suite_identity",
        "sampling_identity",
        "host_session_id",
        "fixed_output_tokens",
    )
    for denominator, candidate in zip(direct, configured):
        if any(getattr(denominator, field) != getattr(candidate, field) for field in pair_fields):
            reasons.append("direct_denominator_identity_mismatch")
        if not denominator.route.supports_positive_claim("direct"):
            reasons.append("direct_route_not_proved")
        if not candidate.route.supports_positive_claim("mtp"):
            reasons.append("mtp_route_not_proved")
        if denominator.output_token_hash != candidate.output_token_hash:
            reasons.append("output_token_identity_mismatch")
        if not denominator.count_reconciles or not candidate.count_reconciles:
            reasons.append("token_counts_do_not_reconcile")
        if candidate.counts.drafted == 0:
            reasons.append("zero_drafted_tokens")
        if not denominator.completed or not candidate.completed or denominator.error or candidate.error:
            reasons.append("incomplete_or_error_trial")

    fallback_count = sum(trial.fallback for trial in configured)
    if fallback_count:
        reasons.append("fallback_observed")
    if sampling_mode != "greedy" and not reproducibly_coupled_sampling:
        reasons.append("sampling_not_reproducibly_coupled")

    drafted = sum(trial.counts.drafted for trial in configured)
    accepted = sum(trial.counts.accepted for trial in configured)
    direct_mean = _mean_elapsed(direct)
    configured_mean = _mean_elapsed(configured)
    if direct_mean is None or configured_mean is None:
        reasons.append("speed_metric_unavailable")
    unique_reasons = tuple(dict.fromkeys(reasons))
    speedup = (
        direct_mean / configured_mean
        if not unique_reasons and direct_mean is not None and configured_mean is not None
        else None
    )
    return MTPAssessment(
        direct_trials=direct,
        configured_trials=configured,
        fallback_count=fallback_count,
        error_count=sum(trial.error for trial in configured),
        drafted_tokens=drafted,
        accepted_draft_tokens=accepted,
        mtp_accept_ratio=(accepted / drafted if drafted else None),
        direct_mean_elapsed_ms=direct_mean,
        configured_mean_elapsed_ms=configured_mean,
        pure_mtp_speedup=speedup,
        pure_speedup_reasons=unique_reasons,
    )
