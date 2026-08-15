from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SCENARIO_SCHEMA = "turnvector.benchmark.scenario.v1"
SUITE_SCHEMA = "turnvector.benchmark.suite.v1"
DRIVER_PROTOCOL = "turnvector.benchmark.driver.v1"

RESOURCE_MODES = {"normal", "guarded", "stop_admission", "critical"}
EXECUTION_PHASES = {"prefill", "decode", "embedding", "residency"}
SERVICE_CLASSES = {"interactive", "standard", "background"}
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ContractError(ValueError):
    """The suite, scenario, or driver violated a versioned contract."""


class ConformanceError(AssertionError):
    """A valid driver response disagreed with the benchmark oracle."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def fraction_map(values: Mapping[str, Fraction]) -> Dict[str, str]:
    return {key: fraction_text(values[key]) for key in sorted(values)}


def _object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _strict_keys(
    value: Mapping[str, Any], required: Iterable[str], optional: Iterable[str], where: str
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError(f"{where} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{where} has unknown fields: {', '.join(unknown)}")


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ContractError(f"{where} must match {IDENTIFIER_RE.pattern}")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be a non-empty string")
    return value


def _integer(value: Any, where: str, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where} must be an integer")
    if value < minimum or value > maximum:
        raise ContractError(f"{where} must be between {minimum} and {maximum}")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where} must be a boolean")
    return value


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    weight: int

    def as_message(self) -> Dict[str, Any]:
        return {"model_id": self.model_id, "weight": self.weight}


@dataclass(frozen=True)
class CandidateTemplate:
    candidate_id: str
    model_id: str
    execution_phase: str
    service_class: str
    engine_service_bound_us: int
    runtime_overhead_bound_us: int
    actual_engine_service_us: int
    timing_obligation_offset_us: Optional[int]
    capability_authorized: bool
    resource_safe: bool
    timing_feasible: bool
    output_reserved: bool

    @property
    def eligible(self) -> bool:
        return (
            self.capability_authorized
            and self.resource_safe
            and self.timing_feasible
            and self.output_reserved
        )

    def at_time(self, now_us: int) -> "Candidate":
        timing_obligation_us = None
        if self.timing_obligation_offset_us is not None:
            timing_obligation_us = now_us + self.timing_obligation_offset_us
        return Candidate(
            candidate_id=self.candidate_id,
            model_id=self.model_id,
            execution_phase=self.execution_phase,
            service_class=self.service_class,
            engine_service_bound_us=self.engine_service_bound_us,
            runtime_overhead_bound_us=self.runtime_overhead_bound_us,
            timing_obligation_us=timing_obligation_us,
            capability_authorized=self.capability_authorized,
            resource_safe=self.resource_safe,
            timing_feasible=self.timing_feasible,
            output_reserved=self.output_reserved,
        )


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    model_id: str
    execution_phase: str
    service_class: str
    engine_service_bound_us: int
    runtime_overhead_bound_us: int
    timing_obligation_us: Optional[int]
    capability_authorized: bool
    resource_safe: bool
    timing_feasible: bool
    output_reserved: bool

    @property
    def eligible(self) -> bool:
        return (
            self.capability_authorized
            and self.resource_safe
            and self.timing_feasible
            and self.output_reserved
        )

    @property
    def latest_safe_start_us(self) -> Optional[int]:
        if self.timing_obligation_us is None:
            return None
        return (
            self.timing_obligation_us
            - self.engine_service_bound_us
            - self.runtime_overhead_bound_us
        )

    def is_urgent(self, now_us: int) -> bool:
        latest = self.latest_safe_start_us
        return self.eligible and latest is not None and now_us >= latest

    def as_message(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_id": self.model_id,
            "execution_phase": self.execution_phase,
            "service_class": self.service_class,
            "engine_service_bound_us": self.engine_service_bound_us,
            "runtime_overhead_bound_us": self.runtime_overhead_bound_us,
            "timing_obligation_us": self.timing_obligation_us,
            "capability_authorized": self.capability_authorized,
            "resource_safe": self.resource_safe,
            "timing_feasible": self.timing_feasible,
            "output_reserved": self.output_reserved,
        }


@dataclass(frozen=True)
class Segment:
    turns: int
    clock_step_us: int
    resource_mode: str
    candidates: Tuple[CandidateTemplate, ...]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    repetitions: int
    clock_start_us: int
    models: Tuple[ModelConfig, ...]
    segments: Tuple[Segment, ...]
    source_path: Path

    @property
    def weights(self) -> Dict[str, int]:
        return {model.model_id: model.weight for model in self.models}

    @property
    def total_turns(self) -> int:
        return sum(segment.turns for segment in self.segments)


@dataclass(frozen=True)
class Suite:
    suite_id: str
    description: str
    scenarios: Tuple[Scenario, ...]
    source_path: Path


def _parse_model(value: Any, where: str) -> ModelConfig:
    obj = _object(value, where)
    _strict_keys(obj, {"model_id", "weight"}, set(), where)
    return ModelConfig(
        model_id=_identifier(obj["model_id"], f"{where}.model_id"),
        weight=_integer(obj["weight"], f"{where}.weight", 1, 1_000_000),
    )


def _parse_candidate(value: Any, where: str, model_ids: Set[str]) -> CandidateTemplate:
    obj = _object(value, where)
    _strict_keys(
        obj,
        {
            "candidate_id",
            "model_id",
            "execution_phase",
            "service_class",
            "engine_service_bound_us",
            "runtime_overhead_bound_us",
            "actual_engine_service_us",
            "timing_obligation_offset_us",
            "capability_authorized",
            "resource_safe",
            "timing_feasible",
            "output_reserved",
        },
        set(),
        where,
    )
    model_id = _identifier(obj["model_id"], f"{where}.model_id")
    if model_id not in model_ids:
        raise ContractError(f"{where}.model_id references unknown model {model_id!r}")
    execution_phase = _string(obj["execution_phase"], f"{where}.execution_phase")
    if execution_phase not in EXECUTION_PHASES:
        raise ContractError(
            f"{where}.execution_phase must be one of {sorted(EXECUTION_PHASES)}"
        )
    service_class = _string(obj["service_class"], f"{where}.service_class")
    if service_class not in SERVICE_CLASSES:
        raise ContractError(f"{where}.service_class must be one of {sorted(SERVICE_CLASSES)}")
    bound = _integer(
        obj["engine_service_bound_us"], f"{where}.engine_service_bound_us", 1
    )
    actual = _integer(
        obj["actual_engine_service_us"], f"{where}.actual_engine_service_us", 1
    )
    if actual > bound:
        raise ContractError(
            f"{where}.actual_engine_service_us exceeds engine_service_bound_us"
        )
    offset_value = obj["timing_obligation_offset_us"]
    offset = None
    if offset_value is not None:
        offset = _integer(offset_value, f"{where}.timing_obligation_offset_us")
    return CandidateTemplate(
        candidate_id=_identifier(obj["candidate_id"], f"{where}.candidate_id"),
        model_id=model_id,
        execution_phase=execution_phase,
        service_class=service_class,
        engine_service_bound_us=bound,
        runtime_overhead_bound_us=_integer(
            obj["runtime_overhead_bound_us"], f"{where}.runtime_overhead_bound_us"
        ),
        actual_engine_service_us=actual,
        timing_obligation_offset_us=offset,
        capability_authorized=_boolean(
            obj["capability_authorized"], f"{where}.capability_authorized"
        ),
        resource_safe=_boolean(obj["resource_safe"], f"{where}.resource_safe"),
        timing_feasible=_boolean(obj["timing_feasible"], f"{where}.timing_feasible"),
        output_reserved=_boolean(obj["output_reserved"], f"{where}.output_reserved"),
    )


def _parse_segment(value: Any, where: str, model_ids: Set[str]) -> Segment:
    obj = _object(value, where)
    _strict_keys(obj, {"turns", "clock_step_us", "resource_mode", "candidates"}, set(), where)
    resource_mode = _string(obj["resource_mode"], f"{where}.resource_mode")
    if resource_mode not in RESOURCE_MODES:
        raise ContractError(f"{where}.resource_mode must be one of {sorted(RESOURCE_MODES)}")
    candidates = tuple(
        _parse_candidate(item, f"{where}.candidates[{index}]", model_ids)
        for index, item in enumerate(_array(obj["candidates"], f"{where}.candidates"))
    )
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    candidate_models = [candidate.model_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ContractError(f"{where}.candidates contains duplicate candidate_id values")
    if len(candidate_models) != len(set(candidate_models)):
        raise ContractError(
            f"{where}.candidates must contain at most one candidate per model in protocol v1"
        )
    return Segment(
        turns=_integer(obj["turns"], f"{where}.turns", 1, 10_000),
        clock_step_us=_integer(obj["clock_step_us"], f"{where}.clock_step_us"),
        resource_mode=resource_mode,
        candidates=candidates,
    )


def load_scenario(path: Path) -> Scenario:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read scenario {path}: {error}") from error
    obj = _object(raw, str(path))
    _strict_keys(
        obj,
        {
            "schema_version",
            "id",
            "description",
            "repetitions",
            "clock_start_us",
            "models",
            "segments",
        },
        set(),
        str(path),
    )
    if obj["schema_version"] != SCENARIO_SCHEMA:
        raise ContractError(
            f"{path}.schema_version must equal {SCENARIO_SCHEMA!r}"
        )
    models = tuple(
        _parse_model(item, f"{path}.models[{index}]")
        for index, item in enumerate(_array(obj["models"], f"{path}.models"))
    )
    if not models:
        raise ContractError(f"{path}.models must not be empty")
    model_ids = [model.model_id for model in models]
    if len(model_ids) != len(set(model_ids)):
        raise ContractError(f"{path}.models contains duplicate model_id values")
    segments = tuple(
        _parse_segment(item, f"{path}.segments[{index}]", set(model_ids))
        for index, item in enumerate(_array(obj["segments"], f"{path}.segments"))
    )
    if not segments:
        raise ContractError(f"{path}.segments must not be empty")
    total_turns = sum(segment.turns for segment in segments)
    if total_turns > 10_000:
        raise ContractError(f"{path} expands to {total_turns} turns; maximum is 10000")
    return Scenario(
        scenario_id=_identifier(obj["id"], f"{path}.id"),
        description=_string(obj["description"], f"{path}.description"),
        repetitions=_integer(obj["repetitions"], f"{path}.repetitions", 2, 10),
        clock_start_us=_integer(obj["clock_start_us"], f"{path}.clock_start_us"),
        models=models,
        segments=segments,
        source_path=path.resolve(),
    )


def load_suite(path: Path) -> Suite:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read suite {path}: {error}") from error
    obj = _object(raw, str(path))
    _strict_keys(
        obj,
        {"schema_version", "id", "description", "scenarios"},
        set(),
        str(path),
    )
    if obj["schema_version"] != SUITE_SCHEMA:
        raise ContractError(f"{path}.schema_version must equal {SUITE_SCHEMA!r}")
    scenario_values = _array(obj["scenarios"], f"{path}.scenarios")
    if not scenario_values:
        raise ContractError(f"{path}.scenarios must not be empty")
    scenarios: List[Scenario] = []
    for index, value in enumerate(scenario_values):
        relative = _string(value, f"{path}.scenarios[{index}]")
        scenarios.append(load_scenario((path.parent / relative).resolve()))
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ContractError(f"{path}.scenarios contains duplicate scenario IDs")
    return Suite(
        suite_id=_identifier(obj["id"], f"{path}.id"),
        description=_string(obj["description"], f"{path}.description"),
        scenarios=tuple(scenarios),
        source_path=path.resolve(),
    )


class SchedulerOracle:
    """Exact oracle for the scheduler-policy subset exercised by protocol v1."""

    def __init__(self, models: Sequence[ModelConfig]) -> None:
        self.weights = {model.model_id: model.weight for model in models}
        self.ledgers = {model.model_id: Fraction(0) for model in models}
        self.runnable: Set[str] = set()
        self.baseline = Fraction(0)
        self.pending: Optional[Candidate] = None

    def _sync_runnable(self, candidates: Sequence[Candidate]) -> Dict[str, Fraction]:
        current = {candidate.model_id for candidate in candidates if candidate.eligible}
        continuing = current & self.runnable
        if continuing:
            alignment = min(self.ledgers[model_id] for model_id in continuing)
        else:
            alignment = self.baseline
        for model_id in current - self.runnable:
            self.ledgers[model_id] = alignment
        self.runnable = current
        if current:
            self.baseline = min(self.ledgers[model_id] for model_id in current)
        return {model_id: self.ledgers[model_id] for model_id in sorted(current)}

    def schedule(
        self, now_us: int, candidates: Sequence[Candidate]
    ) -> Tuple[Optional[Candidate], str, Dict[str, Fraction]]:
        if self.pending is not None:
            raise ContractError("oracle received schedule before the pending receipt")
        runnable_ledgers = self._sync_runnable(candidates)
        eligible = [candidate for candidate in candidates if candidate.eligible]
        urgent = [candidate for candidate in eligible if candidate.is_urgent(now_us)]
        if urgent:
            selected = min(
                urgent,
                key=lambda candidate: (
                    candidate.latest_safe_start_us,
                    self.ledgers[candidate.model_id],
                    candidate.candidate_id,
                ),
            )
            decision_class = "urgent"
        elif eligible:
            selected = min(
                eligible,
                key=lambda candidate: (
                    self.ledgers[candidate.model_id],
                    candidate.model_id,
                    candidate.candidate_id,
                ),
            )
            decision_class = "normal"
        else:
            selected = None
            decision_class = "no_plan"
        self.pending = selected
        return selected, decision_class, runnable_ledgers

    def accept_receipt(
        self, candidate_id: str, model_id: str, actual_engine_service_us: int
    ) -> Dict[str, Fraction]:
        if self.pending is None:
            raise ContractError("oracle received a receipt without a pending plan")
        if self.pending.candidate_id != candidate_id or self.pending.model_id != model_id:
            raise ContractError("oracle receipt does not match the pending plan")
        self.ledgers[model_id] += Fraction(actual_engine_service_us, self.weights[model_id])
        self.pending = None
        if self.runnable:
            self.baseline = min(self.ledgers[item] for item in self.runnable)
        return dict(self.ledgers)

    def clear_no_plan(self) -> None:
        if self.pending is not None:
            raise ContractError("cannot clear a non-empty pending plan")
