"""Cross-engine coverage intersections and fail-closed paired comparisons."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from turnvector_benchmark.core import ContractError, IDENTIFIER_RE


_COMPARISON_FORMS = {"absolute", "paired_delta", "regression"}
_SEMANTIC_CLAIMS = {
    "serving", "route", "prefix_reuse", "mtp", "other_speculative", "determinism"
}


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ContractError("%s must be an identifier" % where)
    return value


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("%s must be a number" % where)
    number = float(value)
    if not math.isfinite(number):
        raise ContractError("%s must be finite" % where)
    return number


@dataclass(frozen=True)
class CoverageIntersection:
    requested: Tuple[str, ...]
    common: Tuple[str, ...]
    left_only: Tuple[str, ...]
    right_only: Tuple[str, ...]
    excluded: Mapping[str, str]

    @property
    def coverage_status(self) -> str:
        if not self.common:
            return "zero_common_cells"
        if len(self.common) == len(self.requested):
            return "complete"
        return "partial"

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "requested_cell_count": len(self.requested),
            "common_supported_cell_count": len(self.common),
            "left_only_cell_count": len(self.left_only),
            "right_only_cell_count": len(self.right_only),
            "requested_cells": list(self.requested),
            "common_cells": list(self.common),
            "left_only_cells": list(self.left_only),
            "right_only_cells": list(self.right_only),
            "excluded": dict(sorted(self.excluded.items())),
            "coverage_status": self.coverage_status,
        }


def coverage_intersection(
    requested_cells: Sequence[str],
    left_status: Mapping[str, str],
    right_status: Mapping[str, str],
    *,
    supported_value: str = "supported",
) -> CoverageIntersection:
    requested = tuple(_identifier(cell, "requested cell") for cell in requested_cells)
    if len(requested) != len(set(requested)):
        raise ContractError("requested cells must be unique")
    left_unknown = set(left_status) - set(requested)
    right_unknown = set(right_status) - set(requested)
    if left_unknown or right_unknown:
        raise ContractError("target capability status contains an unplanned cell")
    common = []
    left_only = []
    right_only = []
    excluded: Dict[str, str] = {}
    for cell in requested:
        left = left_status.get(cell, "not_reported")
        right = right_status.get(cell, "not_reported")
        if left == supported_value and right == supported_value:
            common.append(cell)
        elif left == supported_value:
            left_only.append(cell)
            excluded[cell] = "right:%s" % right
        elif right == supported_value:
            right_only.append(cell)
            excluded[cell] = "left:%s" % left
        else:
            excluded[cell] = "left:%s,right:%s" % (left, right)
    return CoverageIntersection(
        requested, tuple(common), tuple(left_only), tuple(right_only), excluded
    )


@dataclass(frozen=True)
class ComparisonRow:
    cell_id: str
    metric_id: str
    left_target_id: str
    right_target_id: str
    left_value: float
    right_value: float
    ratio_right_over_left: float
    comparison_form: str
    semantic_claim: str
    pairing_key: str

    def as_dict(self) -> Mapping[str, Any]:
        return dict(self.__dict__)


def paired_rows(
    intersection: CoverageIntersection,
    left_target_id: str,
    right_target_id: str,
    metric_id: str,
    left_values: Mapping[str, Any],
    right_values: Mapping[str, Any],
    *,
    comparison_form: str = "paired_delta",
    semantic_claim: str = "serving",
) -> Tuple[ComparisonRow, ...]:
    left_target_id = _identifier(left_target_id, "left target ID")
    right_target_id = _identifier(right_target_id, "right target ID")
    metric_id = _identifier(metric_id, "metric ID")
    if comparison_form not in _COMPARISON_FORMS:
        raise ContractError("invalid comparison form")
    if semantic_claim not in _SEMANTIC_CLAIMS:
        raise ContractError("invalid semantic claim")
    if comparison_form != "absolute" and not intersection.common:
        raise ContractError("paired comparison has zero common cells")
    if set(left_values) != set(intersection.common) or set(right_values) != set(intersection.common):
        raise ContractError("comparison values must equal the common-cell key set")
    rows = []
    for cell in intersection.common:
        left = _finite(left_values[cell], "left metric")
        right = _finite(right_values[cell], "right metric")
        if left <= 0:
            raise ContractError("comparison denominator must be positive")
        rows.append(
            ComparisonRow(
                cell, metric_id, left_target_id, right_target_id, left, right,
                right / left, comparison_form, semantic_claim,
                "%s/%s/%s/%s" % (cell, metric_id, left_target_id, right_target_id),
            )
        )
    return tuple(rows)


def comparison_summary(rows: Iterable[ComparisonRow]) -> Mapping[str, Any]:
    values = tuple(rows)
    if not values:
        raise ContractError("cannot summarize an empty comparison")
    keys = [row.pairing_key for row in values]
    if len(keys) != len(set(keys)):
        raise ContractError("comparison rows contain duplicate pairing keys")
    ratios = [row.ratio_right_over_left for row in values]
    # The aggregate is deliberately fixed as the geometric mean of already-paired ratios.
    geometric = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
    return {
        "paired_row_count": len(values),
        "ratio_direction": "right_over_left",
        "aggregate_reducer": "geometric_mean_of_paired_ratios",
        "aggregate_ratio": geometric,
    }
