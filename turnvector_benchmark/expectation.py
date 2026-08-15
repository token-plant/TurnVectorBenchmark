from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import reduce
from operator import mul
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .core import ContractError, IDENTIFIER_RE, Suite


EXPECTATION_SCHEMA = "turnvector.benchmark.expectation.v1"
HARNESS_STATUSES = {"executable", "contract_only"}
HARNESS_KINDS = {"jsonl_suite", "adapter"}
GATE_OPERATORS = {"eq", "gte", "lte", "present"}
THRESHOLD_SOURCES = {"benchmark_contract", "certification_record", "run_manifest"}

REQUIRED_LANE_POLICY = "all_required_lanes_must_execute_and_pass"
CONTRACT_ONLY_POLICY = "required_but_not_yet_executable"
SCOPE_POLICY = "claims_are_limited_to_exact_evidence_identity"


def _object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


def _strict_keys(
    value: Mapping[str, Any], required: Sequence[str], optional: Sequence[str], where: str
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError(f"{where} is missing required fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{where} has unknown fields: {', '.join(unknown)}")


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be a non-empty string")
    return value


def _identifier(value: Any, where: str) -> str:
    text = _string(value, where)
    if not IDENTIFIER_RE.fullmatch(text):
        raise ContractError(f"{where} must match {IDENTIFIER_RE.pattern}")
    return text


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{where} must be a boolean")
    return value


def _string_array(value: Any, where: str, *, identifiers: bool = False) -> Tuple[str, ...]:
    items = _array(value, where)
    if not items:
        raise ContractError(f"{where} must not be empty")
    parser = _identifier if identifiers else _string
    parsed = tuple(parser(item, f"{where}[{index}]") for index, item in enumerate(items))
    if len(parsed) != len(set(parsed)):
        raise ContractError(f"{where} must not contain duplicate values")
    return parsed


def _scalar(value: Any, where: str) -> Any:
    if isinstance(value, (str, bool, int)) and not isinstance(value, float):
        return value
    raise ContractError(f"{where} must be a string, integer, or boolean")


@dataclass(frozen=True)
class SourceContract:
    repository: str
    revision: str
    clean_required: bool


@dataclass(frozen=True)
class Harness:
    status: str
    kind: str
    protocol: str
    entrypoint: Optional[str]


@dataclass(frozen=True)
class MatrixDimension:
    dimension_id: str
    values: Tuple[Any, ...]


@dataclass(frozen=True)
class Matrix:
    matrix_id: str
    dimensions: Tuple[MatrixDimension, ...]

    @property
    def expanded_case_count(self) -> int:
        return reduce(mul, (len(item.values) for item in self.dimensions), 1)


@dataclass(frozen=True)
class BehaviorCase:
    case_id: str
    requirement: str


@dataclass(frozen=True)
class Gate:
    gate_id: str
    metric: str
    operator: str
    expected: Any
    unit: str
    threshold_source: str
    evidence: Tuple[str, ...]


@dataclass(frozen=True)
class ExpectationLane:
    lane_id: str
    layer: str
    description: str
    required: bool
    implementation_sources: Tuple[str, ...]
    harness: Harness
    matrices: Tuple[Matrix, ...]
    cases: Tuple[BehaviorCase, ...]
    gates: Tuple[Gate, ...]
    required_artifacts: Tuple[str, ...]
    claim_scope: Tuple[str, ...]

    @property
    def expanded_matrix_case_count(self) -> int:
        return sum(matrix.expanded_case_count for matrix in self.matrices)


@dataclass(frozen=True)
class ImplementationExpectation:
    expectation_id: str
    description: str
    source_contract: SourceContract
    lanes: Tuple[ExpectationLane, ...]
    source_path: Path

    def lane(self, lane_id: str) -> ExpectationLane:
        for lane in self.lanes:
            if lane.lane_id == lane_id:
                return lane
        raise ContractError(f"expectation {self.expectation_id!r} has no lane {lane_id!r}")


def _parse_source_contract(value: Any, where: str) -> SourceContract:
    obj = _object(value, where)
    _strict_keys(obj, ["repository", "revision", "clean_required"], [], where)
    revision = _string(obj["revision"], f"{where}.revision")
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise ContractError(f"{where}.revision must be a lowercase 40-character Git SHA")
    return SourceContract(
        repository=_string(obj["repository"], f"{where}.repository"),
        revision=revision,
        clean_required=_boolean(obj["clean_required"], f"{where}.clean_required"),
    )


def _parse_harness(value: Any, where: str) -> Harness:
    obj = _object(value, where)
    _strict_keys(obj, ["status", "kind", "protocol", "entrypoint"], [], where)
    status = _string(obj["status"], f"{where}.status")
    if status not in HARNESS_STATUSES:
        raise ContractError(f"{where}.status must be one of {sorted(HARNESS_STATUSES)}")
    kind = _string(obj["kind"], f"{where}.kind")
    if kind not in HARNESS_KINDS:
        raise ContractError(f"{where}.kind must be one of {sorted(HARNESS_KINDS)}")
    protocol = _identifier(obj["protocol"], f"{where}.protocol")
    raw_entrypoint = obj["entrypoint"]
    entrypoint = None if raw_entrypoint is None else _string(raw_entrypoint, f"{where}.entrypoint")
    if status == "executable" and entrypoint is None:
        raise ContractError(f"{where}.entrypoint is required for an executable harness")
    if status == "contract_only" and entrypoint is not None:
        raise ContractError(f"{where}.entrypoint must be null for a contract-only harness")
    if status == "executable" and kind != "jsonl_suite":
        raise ContractError(f"{where}.kind must be jsonl_suite while the harness is executable")
    return Harness(status=status, kind=kind, protocol=protocol, entrypoint=entrypoint)


def _parse_matrix(value: Any, where: str) -> Matrix:
    obj = _object(value, where)
    _strict_keys(obj, ["id", "dimensions"], [], where)
    dimensions: List[MatrixDimension] = []
    for index, raw_dimension in enumerate(_array(obj["dimensions"], f"{where}.dimensions")):
        dimension_where = f"{where}.dimensions[{index}]"
        dimension_obj = _object(raw_dimension, dimension_where)
        _strict_keys(dimension_obj, ["id", "values"], [], dimension_where)
        values = tuple(
            _scalar(item, f"{dimension_where}.values[{value_index}]")
            for value_index, item in enumerate(_array(dimension_obj["values"], f"{dimension_where}.values"))
        )
        if not values:
            raise ContractError(f"{dimension_where}.values must not be empty")
        if len({json.dumps(item, sort_keys=True) for item in values}) != len(values):
            raise ContractError(f"{dimension_where}.values must not contain duplicates")
        dimensions.append(
            MatrixDimension(
                dimension_id=_identifier(dimension_obj["id"], f"{dimension_where}.id"),
                values=values,
            )
        )
    if not dimensions:
        raise ContractError(f"{where}.dimensions must not be empty")
    dimension_ids = [item.dimension_id for item in dimensions]
    if len(dimension_ids) != len(set(dimension_ids)):
        raise ContractError(f"{where}.dimensions contains duplicate IDs")
    return Matrix(
        matrix_id=_identifier(obj["id"], f"{where}.id"),
        dimensions=tuple(dimensions),
    )


def _parse_case(value: Any, where: str) -> BehaviorCase:
    obj = _object(value, where)
    _strict_keys(obj, ["id", "requirement"], [], where)
    return BehaviorCase(
        case_id=_identifier(obj["id"], f"{where}.id"),
        requirement=_string(obj["requirement"], f"{where}.requirement"),
    )


def _parse_gate(value: Any, where: str) -> Gate:
    obj = _object(value, where)
    _strict_keys(
        obj,
        ["id", "metric", "operator", "expected", "unit", "threshold_source", "evidence"],
        [],
        where,
    )
    operator = _string(obj["operator"], f"{where}.operator")
    if operator not in GATE_OPERATORS:
        raise ContractError(f"{where}.operator must be one of {sorted(GATE_OPERATORS)}")
    threshold_source = _string(obj["threshold_source"], f"{where}.threshold_source")
    if threshold_source not in THRESHOLD_SOURCES:
        raise ContractError(
            f"{where}.threshold_source must be one of {sorted(THRESHOLD_SOURCES)}"
        )
    return Gate(
        gate_id=_identifier(obj["id"], f"{where}.id"),
        metric=_identifier(obj["metric"], f"{where}.metric"),
        operator=operator,
        expected=_scalar(obj["expected"], f"{where}.expected"),
        unit=_identifier(obj["unit"], f"{where}.unit"),
        threshold_source=threshold_source,
        evidence=_string_array(obj["evidence"], f"{where}.evidence", identifiers=True),
    )


def _parse_lane(value: Any, where: str, expectation_path: Path) -> ExpectationLane:
    obj = _object(value, where)
    _strict_keys(
        obj,
        [
            "id",
            "layer",
            "description",
            "required",
            "implementation_sources",
            "harness",
            "matrices",
            "cases",
            "gates",
            "required_artifacts",
            "claim_scope",
        ],
        [],
        where,
    )
    harness = _parse_harness(obj["harness"], f"{where}.harness")
    if harness.entrypoint is not None:
        entrypoint = (expectation_path.parent / harness.entrypoint).resolve()
        if not entrypoint.is_file():
            raise ContractError(f"{where}.harness.entrypoint does not exist: {entrypoint}")
    matrices = tuple(
        _parse_matrix(item, f"{where}.matrices[{index}]")
        for index, item in enumerate(_array(obj["matrices"], f"{where}.matrices"))
    )
    cases = tuple(
        _parse_case(item, f"{where}.cases[{index}]")
        for index, item in enumerate(_array(obj["cases"], f"{where}.cases"))
    )
    gates = tuple(
        _parse_gate(item, f"{where}.gates[{index}]")
        for index, item in enumerate(_array(obj["gates"], f"{where}.gates"))
    )
    for values, label in ((matrices, "matrices"), (cases, "cases"), (gates, "gates")):
        if not values:
            raise ContractError(f"{where}.{label} must not be empty")
    for values, label, attribute in (
        (matrices, "matrices", "matrix_id"),
        (cases, "cases", "case_id"),
        (gates, "gates", "gate_id"),
    ):
        identifiers = [getattr(item, attribute) for item in values]
        if len(identifiers) != len(set(identifiers)):
            raise ContractError(f"{where}.{label} contains duplicate IDs")
    return ExpectationLane(
        lane_id=_identifier(obj["id"], f"{where}.id"),
        layer=_identifier(obj["layer"], f"{where}.layer"),
        description=_string(obj["description"], f"{where}.description"),
        required=_boolean(obj["required"], f"{where}.required"),
        implementation_sources=_string_array(
            obj["implementation_sources"], f"{where}.implementation_sources"
        ),
        harness=harness,
        matrices=matrices,
        cases=cases,
        gates=gates,
        required_artifacts=_string_array(
            obj["required_artifacts"], f"{where}.required_artifacts", identifiers=True
        ),
        claim_scope=_string_array(obj["claim_scope"], f"{where}.claim_scope", identifiers=True),
    )


def load_expectation(path: Path) -> ImplementationExpectation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read expectation {path}: {error}") from error
    obj = _object(raw, str(path))
    _strict_keys(
        obj,
        [
            "schema_version",
            "id",
            "description",
            "source_contract",
            "certification_policy",
            "lanes",
        ],
        [],
        str(path),
    )
    if obj["schema_version"] != EXPECTATION_SCHEMA:
        raise ContractError(f"{path}.schema_version must equal {EXPECTATION_SCHEMA!r}")
    policy = _object(obj["certification_policy"], f"{path}.certification_policy")
    _strict_keys(
        policy,
        ["required_lane_policy", "contract_only_policy", "scope_policy"],
        [],
        f"{path}.certification_policy",
    )
    expected_policy = {
        "required_lane_policy": REQUIRED_LANE_POLICY,
        "contract_only_policy": CONTRACT_ONLY_POLICY,
        "scope_policy": SCOPE_POLICY,
    }
    if policy != expected_policy:
        raise ContractError(
            f"{path}.certification_policy must equal the v1 fail-closed policy"
        )
    lanes = tuple(
        _parse_lane(item, f"{path}.lanes[{index}]", path)
        for index, item in enumerate(_array(obj["lanes"], f"{path}.lanes"))
    )
    if not lanes:
        raise ContractError(f"{path}.lanes must not be empty")
    lane_ids = [lane.lane_id for lane in lanes]
    if len(lane_ids) != len(set(lane_ids)):
        raise ContractError(f"{path}.lanes contains duplicate IDs")
    if not any(lane.required for lane in lanes):
        raise ContractError(f"{path}.lanes must contain at least one required lane")
    return ImplementationExpectation(
        expectation_id=_identifier(obj["id"], f"{path}.id"),
        description=_string(obj["description"], f"{path}.description"),
        source_contract=_parse_source_contract(
            obj["source_contract"], f"{path}.source_contract"
        ),
        lanes=lanes,
        source_path=path.resolve(),
    )


def bind_suite_lane(
    expectation: ImplementationExpectation, lane_id: str, suite: Suite
) -> ExpectationLane:
    lane = expectation.lane(lane_id)
    if lane.harness.status != "executable" or lane.harness.kind != "jsonl_suite":
        raise ContractError(f"lane {lane_id!r} is not an executable JSONL suite")
    assert lane.harness.entrypoint is not None
    entrypoint = (expectation.source_path.parent / lane.harness.entrypoint).resolve()
    if entrypoint != suite.source_path:
        raise ContractError(
            f"lane {lane_id!r} entrypoint {entrypoint} does not match suite {suite.source_path}"
        )
    return lane


def inspect_source_contract(
    expectation: ImplementationExpectation, target_repo: Optional[Path]
) -> Dict[str, Any]:
    if target_repo is None:
        return {"status": "not_checked", "matches": None, "reasons": []}
    root = target_repo.resolve()
    reasons: List[str] = []
    missing_sources = sorted(
        {
            source
            for lane in expectation.lanes
            for source in lane.implementation_sources
            if not (root / source).is_file()
        }
    )
    if missing_sources:
        reasons.append("missing implementation sources: " + ", ".join(missing_sources))
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status_lines = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        return {
            "status": "mismatched",
            "matches": False,
            "reasons": [f"cannot inspect target Git repository: {error}"],
        }
    revision_relation = "exact"
    if head != expectation.source_contract.revision:
        ancestry = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                expectation.source_contract.revision,
                head,
            ],
            capture_output=True,
            text=True,
        )
        if ancestry.returncode != 0:
            revision_relation = "unrelated"
            reasons.append(
                "derivation revision is not an ancestor of the target: "
                f"expected base {expectation.source_contract.revision}, observed {head}"
            )
        else:
            revision_relation = "descendant"
    if expectation.source_contract.clean_required and status_lines:
        reasons.append("target repository is dirty")
    return {
        "status": "matched" if not reasons else "mismatched",
        "matches": not reasons,
        "reasons": reasons,
        "observed_revision": head,
        "revision_relation": revision_relation,
        "dirty": bool(status_lines),
    }


def expectation_summary(
    expectation: ImplementationExpectation, target_repo: Optional[Path]
) -> Dict[str, Any]:
    required = [lane for lane in expectation.lanes if lane.required]
    executable = [lane for lane in required if lane.harness.status == "executable"]
    contract_only = [lane for lane in required if lane.harness.status == "contract_only"]
    return {
        "status": "valid_expectation",
        "expectation_id": expectation.expectation_id,
        "source_contract": inspect_source_contract(expectation, target_repo),
        "required_lane_count": len(required),
        "executable_required_lane_count": len(executable),
        "contract_only_required_lane_count": len(contract_only),
        "unexecutable_required_lane_ids": [lane.lane_id for lane in contract_only],
        "expanded_matrix_case_count": sum(
            lane.expanded_matrix_case_count for lane in required
        ),
        "full_run_available": not contract_only,
        "claim_status": "not_evaluated",
    }
