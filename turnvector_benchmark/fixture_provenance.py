"""Strict typed LaneContext execution-provenance contract for D0 PR 3.

Before the first CasePlan START, every selected lane publishes exactly one
LaneContext ``execution_provenance`` value. Only ``production_subject`` and
``benchmark_fixture`` are admitted. ``fixture_id`` is required exactly for
``benchmark_fixture`` and is forbidden for ``production_subject``; missing,
unknown, or driver/context disagreement fails closed with
:class:`ContractError`. This module is the single authority for that contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Set, Tuple

from .core import ContractError, IDENTIFIER_RE


PRODUCTION_SUBJECT = "production_subject"
BENCHMARK_FIXTURE = "benchmark_fixture"

#: The only admitted execution-provenance values, in frozen order.
EXECUTION_PROVENANCE_VALUES: Tuple[str, ...] = (PRODUCTION_SUBJECT, BENCHMARK_FIXTURE)


def _validate_fixture_id(fixture_id: Any) -> None:
    if not isinstance(fixture_id, str) or not fixture_id:
        raise ContractError(
            "benchmark_fixture execution provenance requires a non-empty fixture_id"
        )
    if not IDENTIFIER_RE.fullmatch(fixture_id):
        raise ContractError(
            f"fixture_id must match identifier grammar {IDENTIFIER_RE.pattern!r}"
        )
    if len(fixture_id) > 128:
        raise ContractError("fixture_id must not exceed 128 UTF-8 characters")


@dataclass(frozen=True)
class ExecutionProvenance:
    """One typed LaneContext execution-provenance value.

    Construction fails closed: the value must be exactly ``production_subject``
    or ``benchmark_fixture``, ``fixture_id`` is required exactly for
    ``benchmark_fixture``, and ``fixture_id`` is forbidden for
    ``production_subject``.
    """

    value: str
    fixture_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or self.value not in EXECUTION_PROVENANCE_VALUES:
            raise ContractError(
                f"unknown execution provenance {self.value!r}; only "
                f"{sorted(EXECUTION_PROVENANCE_VALUES)!r} are admitted"
            )
        if self.value == BENCHMARK_FIXTURE:
            _validate_fixture_id(self.fixture_id)
        elif self.fixture_id is not None:
            raise ContractError(
                "production_subject execution provenance forbids fixture_id; "
                "driver/context disagreement fails closed"
            )

    def as_dict(self) -> dict:
        return {"execution_provenance": self.value, "fixture_id": self.fixture_id}


def validate_execution_provenance(value: Any, fixture_id: Any) -> ExecutionProvenance:
    """Strictly parse one execution-provenance pair; missing/unknown/mismatched fail closed."""
    return ExecutionProvenance(value=value, fixture_id=fixture_id)


@dataclass
class CaseStartMonitor:
    """Mutable per-run monitor shared by LaneController and lane runners.

    Lane runners mark the lane once its first CasePlan START is issued. The
    late-selection boundary is global: once ``first_case_started`` is true in
    any lane, the controller refuses any *new* benchmark fixture selection for
    any lane (a late selection is a contract failure and also leaves the run
    ``fixture_tainted``). Provenance frozen in the pre-run snapshot before any
    START remains usable in later lanes and is never misclassified as late.
    """

    _started_lanes: Set[str] = field(default_factory=set)

    def mark_case_started(self, lane_id: str) -> None:
        """Record that lane_id issued its first CasePlan START (idempotent)."""
        self._started_lanes.add(lane_id)

    def has_started(self, lane_id: str) -> bool:
        return lane_id in self._started_lanes

    @property
    def started_lanes(self) -> Tuple[str, ...]:
        return tuple(sorted(self._started_lanes))

    @property
    def first_case_started(self) -> bool:
        return bool(self._started_lanes)
