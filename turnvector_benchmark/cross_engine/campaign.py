from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_ATTEMPT_STATUSES = frozenset(
    {
        "completed",
        "runtime_error",
        "timeout",
        "crashed",
        "contract_invalid",
        "environment_invalid",
        "interrupted",
    }
)
_RETRYABLE_STATUSES = frozenset({"contract_invalid", "environment_invalid"})


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ContractError(f"{where} must be a lowercase benchmark identifier")
    return value


def _nonnegative_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{where} must be a non-negative integer")
    return value


def balanced_target_orders(
    target_ids: Sequence[str], repetition_count: int
) -> Tuple[Tuple[str, ...], ...]:
    """Return a frozen rotation in which every target occupies every position.

    Target IDs are canonicalized before rotation, so caller ordering cannot bias
    the campaign.  A complete block of ``len(target_ids)`` repetitions is a
    Latin-square rotation.  Alternate blocks are reversed to avoid preserving
    one predecessor direction forever.  For two targets this is the familiar
    AB, BA, BA, AB alternating schedule.
    """

    if isinstance(target_ids, (str, bytes)) or not isinstance(target_ids, Sequence):
        raise ContractError("target_ids must be a sequence of target identifiers")
    parsed = tuple(_identifier(value, "target_ids[]") for value in target_ids)
    if not parsed:
        raise ContractError("target_ids must not be empty")
    if len(parsed) != len(set(parsed)):
        raise ContractError("target_ids must not contain duplicates")
    repetition_count = _nonnegative_integer(repetition_count, "repetition_count")
    if repetition_count == 0:
        raise ContractError("repetition_count must be positive")

    canonical = tuple(sorted(parsed))
    width = len(canonical)
    orders: List[Tuple[str, ...]] = []
    for repetition in range(repetition_count):
        block, offset = divmod(repetition, width)
        base = canonical if block % 2 == 0 else tuple(reversed(canonical))
        orders.append(base[offset:] + base[:offset])
    return tuple(orders)


# Singular spelling kept as the public convenience entry point.
def balanced_target_order(
    target_ids: Sequence[str], repetition_count: int
) -> Tuple[Tuple[str, ...], ...]:
    return balanced_target_orders(target_ids, repetition_count)


def _frozen_parameters(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{where} must be an object")
    if len(value) > 32:
        raise ContractError(f"{where} exceeds the 32-field bound")
    parsed: Dict[str, Any] = {}
    for raw_name, raw_value in value.items():
        name = _identifier(raw_name, f"{where}.<key>")
        if isinstance(raw_value, (str, bool, int)):
            parsed[name] = raw_value
        elif isinstance(raw_value, float) and math.isfinite(raw_value):
            parsed[name] = raw_value
        else:
            raise ContractError(f"{where}.{name} must be a finite JSON scalar")
    return MappingProxyType(dict(sorted(parsed.items())))


@dataclass(frozen=True)
class CampaignCell:
    ordinal: int
    cell_id: str
    pairing_key: str
    target_id: str
    scenario_id: str
    case_id: str
    repetition: int
    required_capabilities: Tuple[str, ...] = ()
    isolation_policy: str = "fresh_process_fresh_state_root"
    matrix_id: str = "default"
    pairing_id: Optional[str] = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonnegative_integer(self.ordinal, "CampaignCell.ordinal")
        _identifier(self.cell_id, "CampaignCell.cell_id")
        _identifier(self.pairing_key, "CampaignCell.pairing_key")
        _identifier(self.target_id, "CampaignCell.target_id")
        _identifier(self.scenario_id, "CampaignCell.scenario_id")
        _identifier(self.case_id, "CampaignCell.case_id")
        _identifier(self.matrix_id, "CampaignCell.matrix_id")
        pairing_id = self.case_id if self.pairing_id is None else self.pairing_id
        object.__setattr__(self, "pairing_id", _identifier(pairing_id, "CampaignCell.pairing_id"))
        object.__setattr__(
            self,
            "parameters",
            _frozen_parameters(self.parameters, "CampaignCell.parameters"),
        )
        _nonnegative_integer(self.repetition, "CampaignCell.repetition")
        capabilities = tuple(
            _identifier(item, "CampaignCell.required_capabilities[]")
            for item in self.required_capabilities
        )
        if len(capabilities) != len(set(capabilities)) or capabilities != tuple(
            sorted(capabilities)
        ):
            raise ContractError(
                "CampaignCell.required_capabilities must be unique and sorted"
            )
        if self.isolation_policy not in {
            "fresh_process_fresh_state_root",
            "fresh_process_preserved_disk_state",
            "session_reuse_memory_warm",
            "no_reset",
        }:
            raise ContractError("CampaignCell.isolation_policy is not registered")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "cell_id": self.cell_id,
            "pairing_key": self.pairing_key,
            "target_id": self.target_id,
            "scenario_id": self.scenario_id,
            "case_id": self.case_id,
            "matrix_id": self.matrix_id,
            "pairing_id": self.pairing_id,
            "parameters": dict(self.parameters),
            "repetition": self.repetition,
            "required_capabilities": list(self.required_capabilities),
            "isolation_policy": self.isolation_policy,
        }


@dataclass(frozen=True)
class CampaignPlan:
    campaign_id: str
    cells: Tuple[CampaignCell, ...]
    target_orders: Tuple[Tuple[str, ...], ...]
    retryable_reason_codes: Tuple[str, ...] = ()
    outlier_policy: str = "no_deletion"

    def __post_init__(self) -> None:
        _identifier(self.campaign_id, "CampaignPlan.campaign_id")
        if not self.cells:
            raise ContractError("CampaignPlan.cells must not be empty")
        if tuple(cell.ordinal for cell in self.cells) != tuple(range(len(self.cells))):
            raise ContractError("CampaignPlan cell ordinals must be contiguous from zero")
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ContractError("CampaignPlan cell IDs must be unique")
        reasons = tuple(
            _identifier(item, "CampaignPlan.retryable_reason_codes[]")
            for item in self.retryable_reason_codes
        )
        if reasons != tuple(sorted(set(reasons))):
            raise ContractError(
                "CampaignPlan.retryable_reason_codes must be unique and sorted"
            )
        if self.outlier_policy != "no_deletion":
            raise ContractError("cross-engine v1 fixes outlier_policy to no_deletion")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "cells": [cell.as_dict() for cell in self.cells],
            "target_orders": [list(order) for order in self.target_orders],
            "retryable_reason_codes": list(self.retryable_reason_codes),
            "outlier_policy": self.outlier_policy,
        }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def freeze_campaign(
    *,
    campaign_id: str,
    cases: Sequence[Any],
    target_ids: Sequence[str],
    repetition_count: int,
    retryable_reason_codes: Sequence[str] = (),
) -> CampaignPlan:
    """Freeze scenario cases into target-balanced, stable campaign cells.

    ``cases`` may be Slice-A case objects or mappings. Their public ``case_id``,
    ``pairing_id``, ``scenario_id``, ``matrix_id``, scalar ``parameters``,
    ``required_capabilities`` and optional ``isolation_policy`` projections are
    consumed; target manifests cannot alter the scenario plan.
    """

    if isinstance(cases, (str, bytes)) or not isinstance(cases, Sequence) or not cases:
        raise ContractError("cases must be a non-empty sequence")
    orders = balanced_target_orders(target_ids, repetition_count)
    parsed_cases: List[
        Tuple[str, str, str, str, Mapping[str, Any], Tuple[str, ...], str]
    ] = []
    for index, case in enumerate(cases):
        case_id = _identifier(_field(case, "case_id"), f"cases[{index}].case_id")
        scenario_id = _identifier(
            _field(case, "scenario_id", _field(case, "family")),
            f"cases[{index}].scenario_id",
        )
        matrix_id = _identifier(
            _field(case, "matrix_id", "default"), f"cases[{index}].matrix_id"
        )
        pairing_id = _identifier(
            _field(case, "pairing_id", case_id), f"cases[{index}].pairing_id"
        )
        parameters = _frozen_parameters(
            _field(case, "parameters", {}), f"cases[{index}].parameters"
        )
        raw_capabilities = _field(case, "required_capabilities", ())
        if isinstance(raw_capabilities, (str, bytes)) or not isinstance(
            raw_capabilities, Sequence
        ):
            raise ContractError(
                f"cases[{index}].required_capabilities must be a sequence"
            )
        capabilities = tuple(
            sorted(
                _identifier(item, f"cases[{index}].required_capabilities[]")
                for item in raw_capabilities
            )
        )
        if len(capabilities) != len(set(capabilities)):
            raise ContractError(
                f"cases[{index}].required_capabilities must not contain duplicates"
            )
        isolation = _field(
            case, "isolation_policy", "fresh_process_fresh_state_root"
        )
        parsed_cases.append(
            (
                case_id,
                scenario_id,
                matrix_id,
                pairing_id,
                parameters,
                capabilities,
                isolation,
            )
        )

    # Slice A already supplies lexical scenario/matrix order.  Repetition is
    # outside that order, then each repetition uses the frozen target rotation.
    cells: List[CampaignCell] = []
    for repetition, target_order in enumerate(orders):
        for (
            case_id,
            scenario_id,
            matrix_id,
            pairing_id,
            parameters,
            capabilities,
            isolation,
        ) in parsed_cases:
            pairing_key = f"{scenario_id}.{case_id}.r{repetition:04d}"
            for target_id in target_order:
                ordinal = len(cells)
                cells.append(
                    CampaignCell(
                        ordinal=ordinal,
                        cell_id=f"{pairing_key}.{target_id}",
                        pairing_key=pairing_key,
                        target_id=target_id,
                        scenario_id=scenario_id,
                        case_id=case_id,
                        repetition=repetition,
                        required_capabilities=capabilities,
                        isolation_policy=isolation,
                        matrix_id=matrix_id,
                        pairing_id=pairing_id,
                        parameters=parameters,
                    )
                )
    return CampaignPlan(
        campaign_id=_identifier(campaign_id, "campaign_id"),
        cells=tuple(cells),
        target_orders=orders,
        retryable_reason_codes=tuple(sorted(retryable_reason_codes)),
    )


@dataclass(frozen=True)
class AttemptRecord:
    attempt_ordinal: int
    cell_id: str
    status: str
    reason_code: Optional[str]
    retry_of: Optional[int]
    eligible_for_primary: bool
    started_monotonic_ns: int
    finished_monotonic_ns: int
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonnegative_integer(self.attempt_ordinal, "AttemptRecord.attempt_ordinal")
        _identifier(self.cell_id, "AttemptRecord.cell_id")
        if self.status not in _ATTEMPT_STATUSES:
            raise ContractError(f"unknown attempt status {self.status!r}")
        if self.reason_code is not None:
            _identifier(self.reason_code, "AttemptRecord.reason_code")
        if self.retry_of is not None:
            _nonnegative_integer(self.retry_of, "AttemptRecord.retry_of")
            if self.retry_of >= self.attempt_ordinal:
                raise ContractError("AttemptRecord.retry_of must name an earlier attempt")
        _nonnegative_integer(
            self.started_monotonic_ns, "AttemptRecord.started_monotonic_ns"
        )
        _nonnegative_integer(
            self.finished_monotonic_ns, "AttemptRecord.finished_monotonic_ns"
        )
        if self.finished_monotonic_ns < self.started_monotonic_ns:
            raise ContractError("attempt finished before it started")
        if not isinstance(self.details, Mapping):
            raise ContractError("AttemptRecord.details must be an object")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "attempt_ordinal": self.attempt_ordinal,
            "cell_id": self.cell_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "retry_of": self.retry_of,
            "eligible_for_primary": self.eligible_for_primary,
            "started_monotonic_ns": self.started_monotonic_ns,
            "finished_monotonic_ns": self.finished_monotonic_ns,
            "details": dict(self.details),
        }


class AttemptLedger:
    """Append-only campaign attempt custody with frozen selective retry rules."""

    def __init__(
        self,
        path: Path,
        *,
        retryable_reason_codes: Sequence[str] = (),
        max_attempts_per_cell: int = 2,
    ) -> None:
        if path.exists():
            raise ContractError("attempt ledger must be created at an absent path")
        if not path.parent.is_dir():
            raise ContractError("attempt ledger parent must already exist")
        self.path = path
        reasons = tuple(
            sorted(
                _identifier(item, "retryable_reason_codes[]")
                for item in retryable_reason_codes
            )
        )
        if len(reasons) != len(set(reasons)):
            raise ContractError("retryable_reason_codes must not contain duplicates")
        self.retryable_reason_codes = frozenset(reasons)
        if (
            isinstance(max_attempts_per_cell, bool)
            or not isinstance(max_attempts_per_cell, int)
            or max_attempts_per_cell < 1
            or max_attempts_per_cell > 16
        ):
            raise ContractError("max_attempts_per_cell must be in [1, 16]")
        self.max_attempts_per_cell = max_attempts_per_cell
        self._records: List[AttemptRecord] = []
        # Create once. Subsequent writes use O_APPEND and never rewrite history.
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)

    @property
    def records(self) -> Tuple[AttemptRecord, ...]:
        return tuple(self._records)

    def records_for(self, cell_id: str) -> Tuple[AttemptRecord, ...]:
        parsed = _identifier(cell_id, "cell_id")
        return tuple(record for record in self._records if record.cell_id == parsed)

    def can_retry(self, record: AttemptRecord) -> bool:
        same_cell = self.records_for(record.cell_id)
        return (
            record.status in _RETRYABLE_STATUSES
            and record.reason_code in self.retryable_reason_codes
            and len(same_cell) < self.max_attempts_per_cell
            and self.primary_attempt(record.cell_id) is None
        )

    def append(
        self,
        *,
        cell_id: str,
        status: str,
        reason_code: Optional[str],
        started_monotonic_ns: int,
        finished_monotonic_ns: int,
        retry_of: Optional[int] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> AttemptRecord:
        prior = self.records_for(cell_id)
        if prior:
            previous = prior[-1]
            if not self.can_retry(previous):
                raise ContractError(
                    "a new attempt is allowed only after a frozen contract/environment retry reason"
                )
            if retry_of != previous.attempt_ordinal:
                raise ContractError("retry_of must name the immediately preceding cell attempt")
        elif retry_of is not None:
            raise ContractError("a first attempt cannot declare retry_of")

        eligible = not (
            status in _RETRYABLE_STATUSES
            and reason_code in self.retryable_reason_codes
        )
        record = AttemptRecord(
            attempt_ordinal=len(self._records),
            cell_id=cell_id,
            status=status,
            reason_code=reason_code,
            retry_of=retry_of,
            eligible_for_primary=eligible,
            started_monotonic_ns=started_monotonic_ns,
            finished_monotonic_ns=finished_monotonic_ns,
            details={} if details is None else dict(details),
        )
        encoded = json.dumps(
            record.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8") + b"\n"
        descriptor = os.open(str(self.path), os.O_WRONLY | os.O_APPEND)
        try:
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise OSError("short append to attempt ledger")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._records.append(record)
        return record

    def primary_attempt(self, cell_id: str) -> Optional[AttemptRecord]:
        for record in self.records_for(cell_id):
            if record.eligible_for_primary:
                return record
        return None


__all__ = [
    "AttemptLedger",
    "AttemptRecord",
    "CampaignCell",
    "CampaignPlan",
    "balanced_target_order",
    "balanced_target_orders",
    "freeze_campaign",
]
