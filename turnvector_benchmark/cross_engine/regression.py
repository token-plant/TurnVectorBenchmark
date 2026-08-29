"""Frozen cross-engine regression gates and promotion decisions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, Tuple, Union

from turnvector_benchmark.core import ContractError, IDENTIFIER_RE


REGRESSION_GATES_SCHEMA = "turnvector.benchmark.cross-engine-regression-gates.v1"
_OPERATORS = {"eq", "gte", "lte"}
_GATE_FIELDS = {"id", "metric", "operator", "expected", "decision"}


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ContractError("%s must be an identifier" % where)
    return value


def _number(value: Any, where: str) -> Union[int, float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("%s must be a number" % where)
    try:
        finite = math.isfinite(value)
    except OverflowError as error:
        raise ContractError("%s must be finite" % where) from error
    if not finite:
        raise ContractError("%s must be finite" % where)
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as error:
        raise ContractError("frozen regression gates are not canonical JSON") from error


@dataclass(frozen=True)
class RegressionGateSpec:
    gate_id: str
    metric: str
    operator: str
    expected: Union[int, float]
    decision: str = "promotion"

    def __post_init__(self) -> None:
        _identifier(self.gate_id, "regression gate ID")
        _identifier(self.metric, "regression gate metric")
        if self.operator not in _OPERATORS:
            raise ContractError("regression gate operator must be eq, gte, or lte")
        _number(self.expected, "regression gate expected value")
        if self.decision != "promotion":
            raise ContractError("regression gates must be promotion gates")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RegressionGateSpec":
        if not isinstance(value, Mapping):
            raise ContractError("regression gate must be an object")
        missing = sorted(_GATE_FIELDS - set(value))
        unknown = sorted(set(value) - _GATE_FIELDS)
        if missing:
            raise ContractError(
                "regression gate is missing required fields: %s" % ", ".join(missing)
            )
        if unknown:
            raise ContractError(
                "regression gate has unknown fields: %s" % ", ".join(unknown)
            )
        return cls(
            value["id"],
            value["metric"],
            value["operator"],
            value["expected"],
            value["decision"],
        )

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "id": self.gate_id,
            "metric": self.metric,
            "operator": self.operator,
            "expected": self.expected,
            "decision": self.decision,
        }


@dataclass(frozen=True)
class FrozenRegressionGates:
    gates: Tuple[RegressionGateSpec, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.gates, tuple):
            raise ContractError("frozen regression gates must be a tuple")
        if not self.gates:
            raise ContractError("frozen regression gates must not be empty")
        if not all(isinstance(gate, RegressionGateSpec) for gate in self.gates):
            raise ContractError("frozen regression gates contain an invalid gate")
        gate_ids = [gate.gate_id for gate in self.gates]
        if len(gate_ids) != len(set(gate_ids)):
            raise ContractError("regression gate IDs must be unique")

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": REGRESSION_GATES_SCHEMA,
            "gates": [gate.as_dict() for gate in self.gates],
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical(self.as_dict())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True)
class RegressionGateResult:
    gate_id: str
    metric: str
    operator: str
    expected: Union[int, float]
    observed: Union[int, float]
    status: str
    decision: str = "promotion"

    def as_dict(self) -> Mapping[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class RegressionEvaluation:
    frozen_gates_sha256: str
    gate_results: Tuple[RegressionGateResult, ...]
    evidence_status: str
    promotion_status: str
    promotion_reasons: Tuple[str, ...]

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "frozen_gates_sha256": self.frozen_gates_sha256,
            "gate_results": [result.as_dict() for result in self.gate_results],
            "evidence_status": self.evidence_status,
            "promotion_status": self.promotion_status,
            "promotion_reasons": list(self.promotion_reasons),
        }


def freeze_regression_gates(
    gates: Sequence[Union[RegressionGateSpec, Mapping[str, Any]]],
) -> FrozenRegressionGates:
    if isinstance(gates, (str, bytes)) or not isinstance(gates, Sequence):
        raise ContractError("regression gates must be an array")
    parsed = tuple(
        gate if isinstance(gate, RegressionGateSpec) else RegressionGateSpec.from_mapping(gate)
        for gate in gates
    )
    return FrozenRegressionGates(parsed)


def _passes(operator: str, observed: Union[int, float], expected: Union[int, float]) -> bool:
    if operator == "eq":
        return observed == expected
    if operator == "lte":
        return observed <= expected
    if operator == "gte":
        return observed >= expected
    raise ContractError("regression gate has an invalid operator")


def evaluate_regression_gates(
    frozen_gates: FrozenRegressionGates,
    observed_metrics: Mapping[str, Any],
) -> RegressionEvaluation:
    if not isinstance(frozen_gates, FrozenRegressionGates):
        raise ContractError("regression gates must be frozen before evaluation")
    if not isinstance(observed_metrics, Mapping):
        raise ContractError("observed regression metrics must be an object")

    results = []
    reasons = []
    for gate in frozen_gates.gates:
        if gate.metric not in observed_metrics:
            raise ContractError(
                "regression metric %r is missing before gate evaluation" % gate.metric
            )
        observed = _number(
            observed_metrics[gate.metric], "observed regression metric %s" % gate.metric
        )
        passed = _passes(gate.operator, observed, gate.expected)
        results.append(
            RegressionGateResult(
                gate.gate_id,
                gate.metric,
                gate.operator,
                gate.expected,
                observed,
                "passed" if passed else "failed",
            )
        )
        if not passed:
            reasons.append("promotion_gate_failed:%s" % gate.gate_id)

    # Regression performance is a promotion decision, not an evidence-validity
    # decision.  A negative result is therefore retained as publishable evidence.
    return RegressionEvaluation(
        frozen_gates.sha256,
        tuple(results),
        "publishable",
        "failed" if reasons else "passed",
        tuple(reasons),
    )
