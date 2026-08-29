from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..core import ContractError
from .routes import RouteEvidence


PREFIX_STATES = (
    "cold_process_cold_cache",
    "warm_process_empty_prefix_cache",
    "warm_memory_prefix_hit",
    "restarted_disk_prefix_hit",
    "post_churn_recovery",
)


def _count(value: int, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{where} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class RestartProof:
    previous_process_identities: Tuple[str, ...]
    current_process_identities: Tuple[str, ...]
    surviving_process_local_state_identities: Tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        previous = set(self.previous_process_identities)
        current = set(self.current_process_identities)
        return (
            bool(previous)
            and bool(current)
            and len(previous) == len(self.previous_process_identities)
            and len(current) == len(self.current_process_identities)
            and all(previous)
            and all(current)
            and previous.isdisjoint(current)
            and not self.surviving_process_local_state_identities
        )


@dataclass(frozen=True)
class PrefixStateRow:
    request_id: str
    state: str
    configured_reuse: bool
    eligible_prefix_tokens: int
    hit_prefix_tokens: int
    block_size_tokens: int
    minimum_reuse_coverage: float
    output_hash: str
    cold_output_hash: str
    route: RouteEvidence
    identities_unchanged: bool
    restart_proof: Optional[RestartProof] = None
    latency_ms: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.request_id or self.route.request_id != self.request_id:
            raise ContractError("prefix row must correlate to its route request_id")
        if not isinstance(self.configured_reuse, bool) or not isinstance(
            self.identities_unchanged, bool
        ):
            raise ContractError(
                "prefix configured_reuse and identities_unchanged must be booleans"
            )
        if self.state not in PREFIX_STATES:
            raise ContractError("prefix row has an unknown state")
        eligible = _count(self.eligible_prefix_tokens, "eligible_prefix_tokens", minimum=1)
        hit = _count(self.hit_prefix_tokens, "hit_prefix_tokens")
        block = _count(self.block_size_tokens, "block_size_tokens", minimum=1)
        if hit > eligible:
            raise ContractError("hit_prefix_tokens cannot exceed eligible_prefix_tokens")
        if (
            isinstance(self.minimum_reuse_coverage, bool)
            or not isinstance(self.minimum_reuse_coverage, (int, float))
            or not math.isfinite(float(self.minimum_reuse_coverage))
            or not 0.0 <= float(self.minimum_reuse_coverage) <= 1.0
        ):
            raise ContractError("minimum_reuse_coverage must be between zero and one")
        if eligible // block == 0:
            raise ContractError("eligible prefix must contain at least one complete cache block")
        if self.latency_ms is not None and (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(float(self.latency_ms))
            or self.latency_ms < 0
        ):
            raise ContractError("latency_ms must be finite and non-negative")

    @property
    def rounded_eligible_tokens(self) -> int:
        return (self.eligible_prefix_tokens // self.block_size_tokens) * self.block_size_tokens

    @property
    def rounded_hit_tokens(self) -> int:
        rounded = (self.hit_prefix_tokens // self.block_size_tokens) * self.block_size_tokens
        return min(rounded, self.rounded_eligible_tokens)

    @property
    def rounded_reuse_coverage(self) -> float:
        return self.rounded_hit_tokens / self.rounded_eligible_tokens

    @property
    def output_parity(self) -> bool:
        return bool(self.output_hash) and self.output_hash == self.cold_output_hash

    @property
    def positive_claim(self) -> bool:
        if not self.configured_reuse or not self.output_parity or not self.identities_unchanged:
            return False
        if self.rounded_reuse_coverage < self.minimum_reuse_coverage:
            return False
        if not self.route.supports_positive_claim("prefix_reuse"):
            return False
        cache = self.route.normalized.cache
        if self.state == "warm_memory_prefix_hit":
            return cache == "memory_prefix"
        if self.state == "restarted_disk_prefix_hit":
            return cache == "disk_prefix" and bool(
                self.restart_proof is not None and self.restart_proof.valid
            )
        return self.state == "post_churn_recovery" and cache in {
            "memory_prefix",
            "disk_prefix",
        }


@dataclass(frozen=True)
class PrefixReuseAggregate:
    rows: Tuple[PrefixStateRow, ...]
    planned_requests: int
    positive_claims: int
    partial_reuse_requests: int
    miss_requests: int
    rounded_reuse_coverage: float
    mean_latency_ms: Optional[float]



def summarize_configured_reuse(
    rows: Sequence[PrefixStateRow], *, planned_request_ids: Sequence[str]
) -> PrefixReuseAggregate:
    """Reduce every planned configured row; misses and partial hits remain."""
    configured = tuple(row for row in rows if row.configured_reuse)
    planned = tuple(planned_request_ids)
    if not planned:
        raise ContractError("configured prefix aggregate must have a non-empty request plan")
    request_ids = tuple(row.request_id for row in configured)
    if len(planned) != len(set(planned)) or len(request_ids) != len(set(request_ids)):
        raise ContractError("configured prefix request IDs must be unique")
    if request_ids != planned:
        raise ContractError(
            "configured prefix rows must retain every planned request in frozen order"
        )
    rounded_eligible = sum(row.rounded_eligible_tokens for row in configured)
    rounded_hit = sum(row.rounded_hit_tokens for row in configured)
    latencies = [float(row.latency_ms) for row in configured if row.latency_ms is not None]
    partial = sum(
        1
        for row in configured
        if 0 < row.rounded_reuse_coverage < row.minimum_reuse_coverage
    )
    misses = sum(1 for row in configured if row.rounded_hit_tokens == 0)
    return PrefixReuseAggregate(
        rows=configured,
        planned_requests=len(configured),
        positive_claims=sum(row.positive_claim for row in configured),
        partial_reuse_requests=partial,
        miss_requests=misses,
        rounded_reuse_coverage=rounded_hit / rounded_eligible,
        mean_latency_ms=(
            sum(latencies) / len(latencies)
            if len(latencies) == len(configured)
            else None
        ),
    )


def validate_prefix_state_rows(rows: Sequence[PrefixStateRow]) -> None:
    observed = {row.state for row in rows}
    missing = [state for state in PREFIX_STATES if state not in observed]
    if missing:
        raise ContractError(f"prefix evidence is missing state rows: {', '.join(missing)}")
