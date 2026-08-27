from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .core import (
    ContractError,
    SchedulerOracle,
    canonical_json,
    fraction_map,
    load_suite,
)
from .data_plane import (
    DataPlaneDescriptor,
    DataPlaneEnvironmentError,
    run_cross_model_case,
    run_generation,
)
from .evidence import (
    CONTROLLER_ARTIFACT_IDS,
    sha256_file,
    validate_subject_artifact,
    write_json,
    write_jsonl,
)
from .expectation import ExpectationLane, Gate
from .fixture_provenance import CaseStartMonitor, validate_execution_provenance
from .lane_contract import (
    CasePlan,
    LaneSuite,
    MetricRecipe,
    PlannedCase,
)
from .lane_oracles import analyze_lane_evidence
from .subject import SubjectHello, SubjectSession
from .system_collectors import ProcessMemorySampler, XctraceCollector


LANE_STATUSES = {
    "passed",
    "gate_failed",
    "unsupported",
    "environment_unavailable",
    "contract_failed",
    "infrastructure_failed",
}


@dataclass(frozen=True)
class LaneContext:
    run_id: str
    lane: ExpectationLane
    suite: LaneSuite
    plan: CasePlan
    artifact_root: Path
    frozen_thresholds: Mapping[str, Any]
    external_inputs: Mapping[str, Mapping[str, Any]]
    #: Strict typed execution provenance: exactly production_subject or
    #: benchmark_fixture; fixture_id is required exactly for benchmark_fixture
    #: and forbidden for production_subject (missing/unknown/mismatched fail).
    execution_provenance: str
    fixture_id: Optional[str]
    #: Per-run mutable monitor shared with LaneController; runners mark the
    #: lane when its first CasePlan START is issued.
    case_start_monitor: CaseStartMonitor

    def __post_init__(self) -> None:
        validate_execution_provenance(self.execution_provenance, self.fixture_id)


@dataclass(frozen=True)
class LaneResult:
    lane_id: str
    status: str
    case_count: int
    executed_case_count: int
    metrics: Mapping[str, Any]
    gates: Tuple[Mapping[str, Any], ...]
    failures: Tuple[Mapping[str, Any], ...]
    artifacts: Tuple[Mapping[str, Any], ...]
    raw_records: Tuple[Mapping[str, Any], ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "status": self.status,
            "case_count": self.case_count,
            "executed_case_count": self.executed_case_count,
            "metrics": dict(self.metrics),
            "gates": [dict(item) for item in self.gates],
            "failures": [dict(item) for item in self.failures],
            "artifacts": [dict(item) for item in self.artifacts],
        }


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be numeric")
    if not math.isfinite(float(value)):
        raise ContractError(f"{where} must be finite")
    return float(value)


def _percentile(samples: Sequence[float], percentile: float) -> float:
    if not samples:
        raise ContractError("percentile reducer received no samples")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def reduce_observations(recipe: MetricRecipe, values: Sequence[Any]) -> Any:
    where = f"metric source {recipe.source!r}"
    if not values:
        raise ContractError(f"{where} has no qualification observations")
    if recipe.reducer == "any":
        if any(not isinstance(value, bool) for value in values):
            raise ContractError(f"{where} must contain booleans for reducer any")
        return any(values)
    if recipe.reducer == "rate":
        operations = 0.0
        seconds = 0.0
        for index, value in enumerate(values):
            if not isinstance(value, dict) or set(value) != {"operations", "seconds"}:
                raise ContractError(
                    f"{where}[{index}] must contain exactly operations and seconds"
                )
            operations += _number(value["operations"], f"{where}[{index}].operations")
            seconds += _number(value["seconds"], f"{where}[{index}].seconds")
        if operations < 0 or seconds <= 0:
            raise ContractError(f"{where} rate totals must have operations >= 0 and seconds > 0")
        return operations / seconds
    if recipe.reducer in {"p50", "p95", "p99"}:
        samples: List[float] = []
        for index, value in enumerate(values):
            if not isinstance(value, list) or not value:
                raise ContractError(f"{where}[{index}] must be a non-empty sample array")
            samples.extend(
                _number(sample, f"{where}[{index}][{sample_index}]")
                for sample_index, sample in enumerate(value)
            )
        percentile = {"p50": 0.50, "p95": 0.95, "p99": 0.99}[recipe.reducer]
        return _percentile(samples, percentile)
    numbers = [_number(value, f"{where}[{index}]") for index, value in enumerate(values)]
    if recipe.reducer == "sum":
        result = sum(numbers)
    elif recipe.reducer == "max":
        result = max(numbers)
    elif recipe.reducer == "min":
        result = min(numbers)
    else:
        raise ContractError(f"unknown reducer {recipe.reducer!r}")
    if all(float(value).is_integer() for value in numbers) and float(result).is_integer():
        return int(result)
    return result


def _gate_passed(operator: str, observed: Any, expected: Any) -> bool:
    if operator == "present":
        return observed is not None
    if isinstance(observed, bool) or isinstance(expected, bool):
        if operator != "eq" or not isinstance(observed, bool) or not isinstance(expected, bool):
            return False
        return observed is expected
    if operator == "eq":
        return observed == expected
    try:
        observed_number = float(observed)
        expected_number = float(expected)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(observed_number) or not math.isfinite(expected_number):
        return False
    if operator == "lte":
        return observed_number <= expected_number
    if operator == "gte":
        return observed_number >= expected_number
    return False


def evaluate_gates(
    lane: ExpectationLane,
    metrics: Mapping[str, Any],
    frozen_thresholds: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], ...]:
    expected_metrics = {gate.metric for gate in lane.gates}
    if set(frozen_thresholds) != expected_metrics:
        raise ContractError(
            f"frozen thresholds for lane {lane.lane_id!r} must exactly match gate metrics"
        )
    results: List[Mapping[str, Any]] = []
    for gate in lane.gates:
        if gate.metric not in metrics:
            raise ContractError(f"metric {gate.metric!r} is missing before gate evaluation")
        expected = frozen_thresholds[gate.metric]
        observed = metrics[gate.metric]
        results.append(
            {
                "gate_id": gate.gate_id,
                "metric": gate.metric,
                "operator": gate.operator,
                "expected": expected,
                "observed": observed,
                "unit": gate.unit,
                "threshold_source": gate.threshold_source,
                "status": "passed" if _gate_passed(gate.operator, observed, expected) else "failed",
            }
        )
    return tuple(results)


def _local_artifact(artifact_id: str, path: Path, root: Path) -> Mapping[str, Any]:
    return {
        "id": artifact_id,
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _lane_result_with_message(
    context: LaneContext,
    status: str,
    message: str,
    *,
    executed: int = 0,
    raw_records: Sequence[Mapping[str, Any]] = (),
) -> LaneResult:
    return LaneResult(
        lane_id=context.lane.lane_id,
        status=status,
        case_count=len(context.plan.cases),
        executed_case_count=executed,
        metrics={},
        gates=(),
        failures=({"kind": status, "message": message},),
        artifacts=(),
        raw_records=tuple(raw_records),
    )


def _benchmark_fixture_inputs(
    lane_id: str, parameters: Mapping[str, Any]
) -> Mapping[str, Any]:
    if lane_id != "protocol-and-worker-supervision":
        return {}
    path = Path(__file__).resolve().parent.parent / "fixtures" / "workers" / "worker_proxy.py"
    if not path.is_file():
        raise ContractError(f"Benchmark Worker fixture is missing: {path}")
    outcome = parameters.get("outcome")
    relation = parameters.get("protocol_relation")
    outcome_modes = {
        "normal": "normal",
        "crash_before_start": "crash-before-start",
        "crash_during_turn": "crash-during-turn",
        "timeout": "timeout",
        "malformed_frame": "malformed-frame",
        "duplicate_receipt": "duplicate-receipt",
    }
    if outcome not in outcome_modes:
        raise ContractError(f"unknown Worker fixture outcome: {outcome!r}")
    if relation not in {"exact", "compatible", "incompatible", "unknown_capability"}:
        raise ContractError(f"unknown Worker protocol relation: {relation!r}")
    mode = (
        "incompatible-handshake"
        if relation in {"incompatible", "unknown_capability"}
        else outcome_modes[outcome]
    )
    max_frame_bytes = 1024
    return {
        "schema_version": "turnvector.benchmark.worker-fixture.v1",
        "command_prefix": [
            sys.executable,
            "-B",
            str(path),
            "--mode",
            mode,
            "--max-frame-bytes",
            str(max_frame_bytes),
            "--timeout-seconds",
            "1",
            "--",
        ],
        "source_sha256": sha256_file(path),
        "mode": mode,
        "max_frame_bytes": max_frame_bytes,
        "protocol_relation": relation,
        "requires_production_worker_command": mode
        not in {"crash-before-start", "timeout", "malformed-frame", "incompatible-handshake"},
    }


CPP_DIRECT_BUNDLE_SCHEMA = "turnvector.benchmark.cpp-direct-bundle.v1"


def _bundle_file(
    root: Path,
    relative_text: Any,
    where: str,
    *,
    require_executable: bool = False,
) -> Path:
    if not isinstance(relative_text, str) or not relative_text:
        raise ContractError(f"{where} must be a non-empty relative path")
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ContractError(f"{where} escapes the C++ Direct bundle")
    resolved_root = root.resolve()
    candidate = resolved_root
    mode = 0
    for component in relative.parts:
        candidate = candidate / component
        try:
            mode = os.lstat(candidate).st_mode
        except OSError as error:
            raise ContractError(f"{where} cannot be inspected: {error}") from error
        if stat.S_ISLNK(mode):
            raise ContractError(f"{where} must not contain a symlink: {candidate}")
    candidate = candidate.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ContractError(f"{where} escapes the C++ Direct bundle") from error
    if not stat.S_ISREG(mode):
        raise ContractError(f"{where} is not a file: {candidate}")
    if require_executable and not mode & 0o111:
        raise ContractError(f"{where} must be executable: {candidate}")
    return candidate


def _positive_int(value: Any, where: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ContractError(f"{where} must be {qualifier}")
    return value


def _load_cpp_direct_bundle(
    descriptor: Mapping[str, Any], benchmark_root: Path
) -> Mapping[str, Any]:
    if descriptor.get("kind") != "directory":
        raise ContractError("cpp-direct-build external input must be a directory")
    root = Path(str(descriptor.get("path", ""))).resolve()
    manifest_path = _bundle_file(root, "manifest.json", "C++ Direct bundle manifest")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read C++ Direct bundle manifest: {error}") from error
    required = {
        "schema_version",
        "binary",
        "seed",
        "warmup",
        "iterations",
        "source_revisions",
        "models",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ContractError("C++ Direct bundle manifest fields are invalid")
    if value["schema_version"] != CPP_DIRECT_BUNDLE_SCHEMA:
        raise ContractError("C++ Direct bundle schema version is invalid")
    lock = json.loads(
        (benchmark_root / "oracles" / "mlx" / "reference-lock-v1.json").read_text(
            encoding="utf-8"
        )
    )
    expected_revisions = {
        name: record["revision"] for name, record in lock["sources"].items()
    }
    if value["source_revisions"] != expected_revisions:
        raise ContractError("C++ Direct bundle source revisions do not match the reference lock")
    if value["seed"] != lock["seed"]:
        raise ContractError("C++ Direct bundle seed does not match the reference lock")
    models = value["models"]
    if not isinstance(models, dict) or set(models) != {"dense", "moe"}:
        raise ContractError("C++ Direct bundle must contain dense and moe model graphs")
    parsed_models: Dict[str, Mapping[str, Any]] = {}
    for architecture, raw in models.items():
        where = f"C++ Direct bundle models.{architecture}"
        fields = {"graph", "layers", "kv_heads", "head_dim", "vocab_size"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ContractError(f"{where} fields are invalid")
        parsed_models[architecture] = {
            "graph": _bundle_file(root, raw["graph"], f"{where}.graph"),
            "layers": _positive_int(raw["layers"], f"{where}.layers"),
            "kv_heads": _positive_int(raw["kv_heads"], f"{where}.kv_heads"),
            "head_dim": _positive_int(raw["head_dim"], f"{where}.head_dim"),
            "vocab_size": _positive_int(raw["vocab_size"], f"{where}.vocab_size"),
        }
    return {
        "root": root,
        "manifest": manifest_path,
        "binary": _bundle_file(
            root,
            value["binary"],
            "C++ Direct bundle binary",
            require_executable=True,
        ),
        "seed": value["seed"],
        "warmup": _positive_int(value["warmup"], "C++ Direct bundle warmup", allow_zero=True),
        "iterations": _positive_int(value["iterations"], "C++ Direct bundle iterations"),
        "models": parsed_models,
    }


def _read_latency_csv(
    path: Path, expected_fields: Sequence[str], where: str
) -> List[Mapping[str, float]]:
    try:
        with path.open(newline="", encoding="ascii") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != list(expected_fields):
                raise ContractError(
                    f"{where} header must be {list(expected_fields)!r}"
                )
            result: List[Mapping[str, float]] = []
            for row_index, row in enumerate(reader):
                if set(row) != set(expected_fields) or any(value is None for value in row.values()):
                    raise ContractError(f"{where} row {row_index} is malformed")
                iteration = int(row["iteration"])
                if iteration != row_index:
                    raise ContractError(f"{where} iteration sequence is not contiguous")
                parsed: Dict[str, float] = {"iteration": float(iteration)}
                for field in expected_fields[1:]:
                    sample = float(row[field])
                    if not math.isfinite(sample) or sample <= 0:
                        raise ContractError(f"{where} {field} samples must be positive and finite")
                    parsed[field] = sample
                result.append(parsed)
    except (OSError, UnicodeError, ValueError) as error:
        raise ContractError(f"cannot read {where}: {error}") from error
    if not result:
        raise ContractError(f"{where} contains no samples")
    return result


def _read_strict_json(path: Path, fields: Sequence[str], where: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read {where}: {error}") from error
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError(f"{where} fields are invalid")
    return value


def _read_strict_jsonl(
    path: Path, fields: Sequence[str], where: str
) -> List[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                if not line.strip():
                    raise ContractError(f"{where} contains a blank line")
                value = json.loads(line)
                if not isinstance(value, dict) or set(value) != set(fields):
                    raise ContractError(f"{where} record {index} fields are invalid")
                records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read {where}: {error}") from error
    if not records:
        raise ContractError(f"{where} contains no records")
    return records


def _validate_observations(
    suite: LaneSuite, case: PlannedCase, observations: Mapping[str, Any]
) -> None:
    expected = {recipe.source for recipe in suite.metrics}
    observed = set(observations)
    if observed != expected:
        raise ContractError(
            f"{case.case_id} observations must exactly match suite sources: "
            f"missing={sorted(expected - observed)!r}, unknown={sorted(observed - expected)!r}"
        )


class LaneRunner:
    lane_id: str

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        raise NotImplementedError


class EvidenceLaneRunner(LaneRunner):
    """Owns case orchestration, raw-observation reduction, artifacts, and gates."""

    def begin_case_collection(
        self, context: LaneContext, hello: SubjectHello, case: PlannedCase
    ) -> Any:
        del context, hello, case
        return None

    def end_case_collection(self, collector: Any) -> Optional[Mapping[str, Any]]:
        del collector
        return None

    def execute_case_steps(
        self,
        context: LaneContext,
        subject: SubjectSession,
        hello: SubjectHello,
        case: PlannedCase,
        benchmark_runtime_root: Optional[Path],
    ) -> Tuple[
        List[Mapping[str, Any]],
        List[Mapping[str, Any]],
        Optional[Mapping[str, Any]],
    ]:
        step_evidence: List[Mapping[str, Any]] = []
        step_records: List[Mapping[str, Any]] = []
        for step_index, operation in enumerate(case.operations, start=1):
            evidence = subject.case_step(
                case.case_id,
                step_index,
                operation,
                {
                    "lane_id": case.lane_id,
                    "matrix_id": case.matrix_id,
                    "parameters": dict(case.parameters),
                    "behavior_case_ids": list(case.behavior_case_ids),
                    "execution_boundary": context.suite.requirements.execution_boundary,
                    "data_plane": hello.data_plane,
                    "external_inputs": dict(context.external_inputs),
                    "benchmark_fixtures": _benchmark_fixture_inputs(
                        context.lane.lane_id, case.parameters
                    ),
                    "benchmark_runtime_root": (
                        None
                        if benchmark_runtime_root is None
                        else str(benchmark_runtime_root)
                    ),
                },
            )
            step_evidence.append(dict(evidence))
            step_records.append(
                {
                    "case_id": case.case_id,
                    "phase": "step",
                    "step_index": step_index,
                    "operation": operation,
                    "evidence": dict(evidence),
                }
            )
        return step_evidence, step_records, None

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        del context, case_collections
        return ()

    def analyze_case(
        self,
        context: LaneContext,
        case: PlannedCase,
        step_evidence: Sequence[Mapping[str, Any]],
        close_observations: Mapping[str, Any],
        collection: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        del collection
        return analyze_lane_evidence(
            context.lane.lane_id,
            case.parameters,
            step_evidence,
            close_observations,
        )

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        raw_records: List[Mapping[str, Any]] = []
        observations: Dict[str, List[Any]] = {
            recipe.source: [] for recipe in context.suite.metrics
        }
        artifacts: List[Mapping[str, Any]] = []
        artifact_by_id: Dict[str, Mapping[str, Any]] = {}
        case_collections: List[Mapping[str, Any]] = []
        executed = 0
        for case in context.plan.cases:
            case_directory = f"cases/{case.ordinal:04d}"
            case_root = context.artifact_root / case_directory
            case_root.mkdir(parents=True, exist_ok=False)
            benchmark_runtime_root: Optional[Path] = None
            if context.lane.lane_id == "persistence-and-recovery":
                benchmark_runtime_root = case_root / "runtime-root"
                benchmark_runtime_root.mkdir()
            context.case_start_monitor.mark_case_started(context.lane.lane_id)
            open_status = subject.case_open(
                context.run_id, case, context.artifact_root, case_directory
            )
            raw_records.append(
                {"case_id": case.case_id, "phase": "open", "status": open_status}
            )
            if open_status != "ready":
                return LaneResult(
                    lane_id=context.lane.lane_id,
                    status=open_status,
                    case_count=len(context.plan.cases),
                    executed_case_count=executed,
                    metrics={},
                    gates=(),
                    failures=(
                        {
                            "kind": open_status,
                            "case_id": case.case_id,
                            "message": f"subject returned {open_status} from case_open",
                        },
                    ),
                    artifacts=tuple(artifacts),
                    raw_records=tuple(raw_records),
                )
            collector = self.begin_case_collection(context, hello, case)
            collection: Optional[Mapping[str, Any]] = None
            system_collection: Optional[Mapping[str, Any]] = None
            case_error: Optional[Exception] = None
            try:
                step_evidence, step_records, collection = self.execute_case_steps(
                    context,
                    subject,
                    hello,
                    case,
                    benchmark_runtime_root,
                )
                raw_records.extend(step_records)
                case_observations, case_artifacts = subject.case_close(case.case_id)
            except Exception as error:
                case_error = error
                raise
            finally:
                try:
                    system_collection = self.end_case_collection(collector)
                except Exception as collector_error:
                    write_json(
                        case_root / "collector-cleanup-error.json",
                        {
                            "case_id": case.case_id,
                            "case_error": (
                                None
                                if case_error is None
                                else {
                                    "type": type(case_error).__name__,
                                    "message": str(case_error),
                                }
                            ),
                            "collector_error": {
                                "type": type(collector_error).__name__,
                                "message": str(collector_error),
                            },
                        },
                    )
                    if case_error is None:
                        raise
                    raise ContractError(
                        f"{case.case_id}: {case_error}; "
                        f"collector cleanup failed: {collector_error}"
                    ) from case_error
            if collection is not None and system_collection is not None:
                raise ContractError(
                    f"lane {context.lane.lane_id!r} produced two independent case collections"
                )
            if collection is None:
                collection = system_collection
            if collection is not None:
                case_collections.append(
                    {
                        "case_id": case.case_id,
                        "parameters": dict(case.parameters),
                        "evidence": dict(collection),
                    }
                )
                raw_records.append(
                    {
                        "case_id": case.case_id,
                        "phase": "benchmark_collection",
                        "evidence": dict(collection),
                    }
                )
            reduced_case = self.analyze_case(
                context,
                case,
                step_evidence,
                case_observations,
                collection,
            )
            _validate_observations(context.suite, case, reduced_case)
            for source, value in reduced_case.items():
                observations[source].append(value)
            for descriptor in case_artifacts:
                verified = validate_subject_artifact(descriptor, context.artifact_root)
                prior = artifact_by_id.get(verified["id"])
                if prior is not None and prior != verified:
                    raise ContractError(
                        f"artifact ID {verified['id']!r} was reused with different evidence"
                    )
                if prior is None:
                    artifact_by_id[verified["id"]] = verified
                    artifacts.append(verified)
            executed += 1
            raw_records.append(
                {
                    "case_id": case.case_id,
                    "phase": "close",
                    "subject_observations": dict(case_observations),
                    "benchmark_observations": dict(reduced_case),
                    "artifacts": [dict(item) for item in case_artifacts],
                }
            )
        for descriptor in self.benchmark_artifacts(context, case_collections):
            verified = validate_subject_artifact(descriptor, context.artifact_root)
            prior = artifact_by_id.get(verified["id"])
            if prior is not None and prior != verified:
                raise ContractError(
                    f"artifact ID {verified['id']!r} conflicts with Benchmark evidence"
                )
            if prior is None:
                artifact_by_id[verified["id"]] = verified
                artifacts.append(verified)
        required_subject_artifacts = set(context.lane.required_artifacts) - CONTROLLER_ARTIFACT_IDS
        missing_artifacts = sorted(required_subject_artifacts - set(artifact_by_id))
        if missing_artifacts:
            raise ContractError(
                f"lane {context.lane.lane_id!r} is missing required raw artifacts: "
                f"{missing_artifacts!r}"
            )
        metrics = {
            recipe.metric: reduce_observations(recipe, observations[recipe.source])
            for recipe in context.suite.metrics
        }
        gates = evaluate_gates(context.lane, metrics, context.frozen_thresholds)
        failed = [item for item in gates if item["status"] != "passed"]
        return LaneResult(
            lane_id=context.lane.lane_id,
            status="passed" if not failed else "gate_failed",
            case_count=len(context.plan.cases),
            executed_case_count=executed,
            metrics=metrics,
            gates=gates,
            failures=tuple(
                {
                    "kind": "gate_failed",
                    "gate_id": item["gate_id"],
                    "metric": item["metric"],
                    "expected": item["expected"],
                    "observed": item["observed"],
                }
                for item in failed
            ),
            artifacts=tuple(artifacts),
            raw_records=tuple(raw_records),
        )


class RealDaemonEvidenceLaneRunner(EvidenceLaneRunner):
    """Requires a live, protocol-locked production Data Plane for system lanes."""

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        if hello.identity.kind != "fixture":
            try:
                DataPlaneDescriptor.parse(hello.data_plane)
            except DataPlaneEnvironmentError as error:
                return _lane_result_with_message(
                    context, "environment_unavailable", str(error)
                )
        return super().run(context, subject, hello)


def _parse_fraction_map(
    value: Any, expected_models: Iterable[str], where: str
) -> Dict[str, Fraction]:
    if not isinstance(value, dict) or set(value) != set(expected_models):
        raise ContractError(f"{where} must contain the exact model ledger keys")
    result: Dict[str, Fraction] = {}
    for model_id, text in value.items():
        if not isinstance(text, str):
            raise ContractError(f"{where}.{model_id} must be a canonical fraction string")
        try:
            parsed = Fraction(text)
        except (ValueError, ZeroDivisionError) as error:
            raise ContractError(f"{where}.{model_id} is not a fraction") from error
        if f"{parsed.numerator}/{parsed.denominator}" != text:
            raise ContractError(f"{where}.{model_id} is not canonical")
        result[model_id] = parsed
    return result


class SchedulerPolicyLaneRunner(LaneRunner):
    lane_id = "scheduler-policy"

    _SCENARIOS = {
        "weighted_service": "weighted-service-1-to-3",
        "idle_reentry": "idle-reentry-no-credit",
        "urgency_after_safety": "urgency-after-safety",
    }

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        legacy_entrypoint = context.lane.harness.legacy_entrypoint
        if legacy_entrypoint is None:
            raise ContractError("scheduler-policy lane lost its legacy oracle suite")
        expectation_dir = context.suite.source_path.parents[2] / "expectations"
        suite_path = (expectation_dir / legacy_entrypoint).resolve()
        legacy_suite = load_suite(suite_path)
        scenarios = {scenario.scenario_id: scenario for scenario in legacy_suite.scenarios}
        raw_records: List[Mapping[str, Any]] = []
        selection_mismatches = 0
        receipt_mismatches = 0
        replay_mismatches = 0
        executed = 0
        artifacts: List[Mapping[str, Any]] = []
        for case in context.plan.cases:
            value = case.parameters.get("scenario")
            scenario_id = self._SCENARIOS.get(str(value))
            if scenario_id is None or scenario_id not in scenarios:
                raise ContractError(f"scheduler case {case.case_id} has unknown scenario {value!r}")
            scenario = scenarios[scenario_id]
            case_directory = f"cases/{case.ordinal:04d}"
            (context.artifact_root / case_directory).mkdir(parents=True, exist_ok=False)
            context.case_start_monitor.mark_case_started(context.lane.lane_id)
            open_status = subject.case_open(
                context.run_id, case, context.artifact_root, case_directory
            )
            if open_status != "ready":
                return LaneResult(
                    lane_id=context.lane.lane_id,
                    status=open_status,
                    case_count=len(context.plan.cases),
                    executed_case_count=executed,
                    metrics={},
                    gates=(),
                    failures=(
                        {
                            "kind": open_status,
                            "case_id": case.case_id,
                            "message": f"subject returned {open_status} from case_open",
                        },
                    ),
                    artifacts=tuple(artifacts),
                    raw_records=tuple(raw_records),
                )
            repetition_hashes: List[str] = []
            step_index = 0
            for repetition in range(scenario.repetitions):
                oracle = SchedulerOracle(scenario.models)
                step_index += 1
                initialized = subject.case_step(
                    case.case_id,
                    step_index,
                    "scheduler_initialize",
                    {
                        "scenario_id": scenario.scenario_id,
                        "repetition": repetition,
                        "models": [model.as_message() for model in scenario.models],
                    },
                )
                initialized_ledgers = _parse_fraction_map(
                    initialized.get("model_ledgers_us"),
                    [model.model_id for model in scenario.models],
                    "scheduler_initialize.model_ledgers_us",
                )
                if any(value != 0 for value in initialized_ledgers.values()):
                    receipt_mismatches += 1
                now_us = scenario.clock_start_us
                sequence = 0
                repetition_records: List[Mapping[str, Any]] = []
                aborted = False
                for segment_index, segment in enumerate(scenario.segments):
                    if aborted:
                        break
                    for segment_turn in range(segment.turns):
                        sequence += 1
                        candidates = [template.at_time(now_us) for template in segment.candidates]
                        actual_by_id = {
                            template.candidate_id: template.actual_engine_service_us
                            for template in segment.candidates
                        }
                        expected, decision_class, runnable = oracle.schedule(now_us, candidates)
                        expected_id = expected.candidate_id if expected else None
                        step_index += 1
                        observed = subject.case_step(
                            case.case_id,
                            step_index,
                            "scheduler_schedule",
                            {
                                "sequence": sequence,
                                "now_us": now_us,
                                "resource_mode": segment.resource_mode,
                                "candidates": [candidate.as_message() for candidate in candidates],
                            },
                        )
                        if set(observed) != {"candidate_id", "runnable_ledgers_us"}:
                            raise ContractError("scheduler_schedule evidence has invalid fields")
                        observed_id = observed["candidate_id"]
                        if observed_id is not None and not isinstance(observed_id, str):
                            raise ContractError("scheduler_schedule candidate_id must be string or null")
                        observed_runnable = _parse_fraction_map(
                            observed["runnable_ledgers_us"], runnable, "runnable_ledgers_us"
                        )
                        selection_ok = observed_id == expected_id and observed_runnable == runnable
                        if not selection_ok:
                            selection_mismatches += 1
                            aborted = True
                        record: Dict[str, Any] = {
                            "case_id": case.case_id,
                            "scenario_id": scenario.scenario_id,
                            "repetition": repetition,
                            "sequence": sequence,
                            "segment_index": segment_index,
                            "segment_turn": segment_turn,
                            "now_us": now_us,
                            "decision_class": decision_class,
                            "expected_candidate_id": expected_id,
                            "observed_candidate_id": observed_id,
                            "expected_runnable_ledgers_us": fraction_map(runnable),
                            "observed_runnable_ledgers_us": fraction_map(observed_runnable),
                            "selection_ok": selection_ok,
                        }
                        if selection_ok and expected is not None:
                            actual = actual_by_id[expected.candidate_id]
                            expected_ledgers = oracle.accept_receipt(
                                expected.candidate_id, expected.model_id, actual
                            )
                            step_index += 1
                            receipt = subject.case_step(
                                case.case_id,
                                step_index,
                                "scheduler_receipt",
                                {
                                    "sequence": sequence,
                                    "candidate_id": expected.candidate_id,
                                    "model_id": expected.model_id,
                                    "actual_engine_service_us": actual,
                                },
                            )
                            if set(receipt) != {"model_ledgers_us"}:
                                raise ContractError("scheduler_receipt evidence has invalid fields")
                            observed_ledgers = _parse_fraction_map(
                                receipt["model_ledgers_us"],
                                expected_ledgers,
                                "scheduler_receipt.model_ledgers_us",
                            )
                            receipt_ok = observed_ledgers == expected_ledgers
                            if not receipt_ok:
                                receipt_mismatches += 1
                                aborted = True
                            record["expected_receipt_ledgers_us"] = fraction_map(expected_ledgers)
                            record["observed_receipt_ledgers_us"] = fraction_map(observed_ledgers)
                            record["receipt_ok"] = receipt_ok
                        elif selection_ok:
                            oracle.clear_no_plan()
                        repetition_records.append(record)
                        raw_records.append(record)
                        now_us += segment.clock_step_us
                        if aborted:
                            break
                replay_identity = [
                    {key: value for key, value in record.items() if key != "repetition"}
                    for record in repetition_records
                ]
                digest = hashlib.sha256(
                    canonical_json(replay_identity).encode("utf-8")
                ).hexdigest()
                repetition_hashes.append(digest)
            if repetition_hashes:
                replay_mismatches += sum(
                    value != repetition_hashes[0] for value in repetition_hashes[1:]
                )
            case_observations, case_artifacts = subject.case_close(case.case_id)
            if case_observations:
                raise ContractError("scheduler-policy case_close observations must be empty")
            for descriptor in case_artifacts:
                artifacts.append(validate_subject_artifact(descriptor, context.artifact_root))
            executed += 1
        trace_path = context.artifact_root / "scheduler-trace.jsonl"
        write_jsonl(trace_path, raw_records)
        trace_artifact = {
            "id": "trace",
            "path": trace_path.relative_to(context.artifact_root).as_posix(),
            "size": trace_path.stat().st_size,
            "sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        }
        artifacts.append(trace_artifact)
        metrics = {
            "oracle_selection_mismatch_count": selection_mismatches,
            "receipt_ledger_mismatch_count": receipt_mismatches,
            "plan_trace_hash_mismatch_count": replay_mismatches,
        }
        gates = evaluate_gates(context.lane, metrics, context.frozen_thresholds)
        failed = [item for item in gates if item["status"] != "passed"]
        return LaneResult(
            lane_id=context.lane.lane_id,
            status="passed" if not failed else "gate_failed",
            case_count=len(context.plan.cases),
            executed_case_count=executed,
            metrics=metrics,
            gates=gates,
            failures=tuple(
                {
                    "kind": "gate_failed",
                    "gate_id": item["gate_id"],
                    "metric": item["metric"],
                    "expected": item["expected"],
                    "observed": item["observed"],
                }
                for item in failed
            ),
            artifacts=tuple(artifacts),
            raw_records=tuple(raw_records),
        )


CORE_RESULT_VALUES = {"completed", "failed", "cancelled", "indeterminate"}
CORE_SEQUENCE_RELATIONS = {
    "current",
    "duplicate",
    "late",
    "unknown",
    "stale_generation",
}


def _core_event_case_input(case: PlannedCase) -> Mapping[str, Any]:
    result = case.parameters.get("result")
    relation = case.parameters.get("sequence_relation")
    if result not in CORE_RESULT_VALUES or relation not in CORE_SEQUENCE_RELATIONS:
        raise ContractError("core replay CasePlan parameters are invalid")
    effect_id = f"effect-{case.ordinal:04d}"
    generation = 7
    sequence = {
        "current": 41,
        "duplicate": 40,
        "late": 39,
        "unknown": 41,
        "stale_generation": 41,
    }[str(relation)]
    receipt_effect_id = "unknown-effect" if relation == "unknown" else effect_id
    return {
        "schema_version": "turnvector.benchmark.core-event-input.v1",
        "case_id": case.case_id,
        "seed": 20260812,
        "initial_state": {
            "generation": generation,
            "last_effect_sequence": 40,
            "pending_effect_ids": (
                [] if relation in {"duplicate", "unknown"} else [effect_id]
            ),
            "seen_effect_ids": [effect_id] if relation == "duplicate" else [],
        },
        "effect_result": {
            "effect_id": receipt_effect_id,
            "generation": generation - 1 if relation == "stale_generation" else generation,
            "sequence": sequence,
            "result": result,
            "sequence_relation": relation,
        },
        "cancellation": (
            {"command_id": f"command-{case.ordinal:04d}", "sequence": 42}
            if result == "cancelled"
            else None
        ),
    }


def _core_sha256(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(f"{where} must be a lowercase SHA256")
    return value


def _core_optional_sequence(value: Any, where: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{where} must be a positive integer or null")
    return value


def _normalize_core_execution(
    value: Any,
    *,
    case_input: Mapping[str, Any],
    input_sha256: str,
    where: str,
) -> Tuple[Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
    execution = _strict_evidence_object(
        value,
        [
            "input_sha256",
            "final_state_sha256",
            "receipt",
            "published_effects",
            "cancellation",
        ],
        where,
    )
    if execution["input_sha256"] != input_sha256:
        raise ContractError(f"{where}.input_sha256 differs from the Benchmark event stream")
    final_state = _core_sha256(
        execution["final_state_sha256"], f"{where}.final_state_sha256"
    )
    receipt = _strict_evidence_object(
        execution["receipt"],
        [
            "sequence_relation",
            "applied",
            "transition_committed",
            "state_before_sha256",
            "state_after_sha256",
        ],
        f"{where}.receipt",
    )
    relation = case_input["effect_result"]["sequence_relation"]
    result = case_input["effect_result"]["result"]
    if receipt["sequence_relation"] not in CORE_SEQUENCE_RELATIONS:
        raise ContractError(f"{where}.receipt.sequence_relation is invalid")
    if not isinstance(receipt["applied"], bool) or not isinstance(
        receipt["transition_committed"], bool
    ):
        raise ContractError(f"{where}.receipt applied/committed flags must be boolean")
    before = _core_sha256(
        receipt["state_before_sha256"], f"{where}.receipt.state_before_sha256"
    )
    after = _core_sha256(
        receipt["state_after_sha256"], f"{where}.receipt.state_after_sha256"
    )
    if final_state != after:
        raise ContractError(f"{where}.final_state_sha256 must equal receipt state_after_sha256")

    raw_effects = execution["published_effects"]
    if not isinstance(raw_effects, list):
        raise ContractError(f"{where}.published_effects must be an array")
    effects: List[Mapping[str, Any]] = []
    for index, raw in enumerate(raw_effects):
        effect = _strict_evidence_object(
            raw,
            ["effect_id", "publication_sequence"],
            f"{where}.published_effects[{index}]",
        )
        if not isinstance(effect["effect_id"], str) or not effect["effect_id"]:
            raise ContractError(
                f"{where}.published_effects[{index}].effect_id must be non-empty"
            )
        publication_sequence = _core_optional_sequence(
            effect["publication_sequence"],
            f"{where}.published_effects[{index}].publication_sequence",
        )
        assert publication_sequence is not None
        effects.append(
            {
                "effect_id": effect["effect_id"],
                "publication_sequence": publication_sequence,
            }
        )

    cancellation = _strict_evidence_object(
        execution["cancellation"],
        ["commit_sequence", "terminal_sequence"],
        f"{where}.cancellation",
    )
    cancel_commit = _core_optional_sequence(
        cancellation["commit_sequence"], f"{where}.cancellation.commit_sequence"
    )
    terminal = _core_optional_sequence(
        cancellation["terminal_sequence"], f"{where}.cancellation.terminal_sequence"
    )
    should_apply = relation == "current"
    should_commit = should_apply and result != "failed"
    unique_effects = len({item["effect_id"] for item in effects}) == len(effects)
    cancellation_expected = result == "cancelled" and should_apply
    cancellation_ordered = (
        cancel_commit is not None
        and terminal is not None
        and cancel_commit <= terminal
        and all(item["publication_sequence"] < cancel_commit for item in effects)
        if cancellation_expected
        else cancel_commit is None and terminal is None
    )
    state_change_valid = (before != after) if should_commit else (before == after)
    no_invalid_effect = should_commit or not effects
    failed_atomic = result != "failed" or (
        not receipt["transition_committed"] and before == after and not effects
    )
    invariant_results = (
        {
            "id": "sequence-relation",
            "passed": receipt["sequence_relation"] == relation,
        },
        {"id": "receipt-application", "passed": receipt["applied"] == should_apply},
        {
            "id": "atomic-transition",
            "passed": receipt["transition_committed"] == should_commit
            and state_change_valid
            and failed_atomic,
        },
        {"id": "effect-idempotence", "passed": unique_effects and no_invalid_effect},
        {"id": "cancellation-order", "passed": cancellation_ordered},
    )
    normalized = {
        "input_sha256": input_sha256,
        "final_state_sha256": final_state,
        "receipt": {
            "sequence_relation": receipt["sequence_relation"],
            "applied": receipt["applied"],
            "transition_committed": receipt["transition_committed"],
            "state_before_sha256": before,
            "state_after_sha256": after,
        },
        "published_effects": effects,
        "cancellation": {
            "commit_sequence": cancel_commit,
            "terminal_sequence": terminal,
        },
    }
    return normalized, invariant_results


class CoreEventReplayLaneRunner(EvidenceLaneRunner):
    lane_id = "core-event-replay"

    def execute_case_steps(
        self,
        context: LaneContext,
        subject: SubjectSession,
        hello: SubjectHello,
        case: PlannedCase,
        benchmark_runtime_root: Optional[Path],
    ) -> Tuple[
        List[Mapping[str, Any]],
        List[Mapping[str, Any]],
        Optional[Mapping[str, Any]],
    ]:
        if hello.identity.kind == "fixture":
            return super().execute_case_steps(
                context, subject, hello, case, benchmark_runtime_root
            )
        if benchmark_runtime_root is not None or tuple(case.operations) != (
            "replay-event-stream",
            "replay-identical-stream",
        ):
            raise ContractError("core replay suite operations are not canonical")
        case_input = _core_event_case_input(case)
        input_sha256 = hashlib.sha256(
            canonical_json(case_input).encode("utf-8")
        ).hexdigest()
        executions: List[Mapping[str, Any]] = []
        invariant_sets: List[Tuple[Mapping[str, Any], ...]] = []
        raw_records: List[Mapping[str, Any]] = []
        for step_index, operation in enumerate(case.operations, start=1):
            evidence = subject.case_step(
                case.case_id,
                step_index,
                operation,
                {
                    "lane_id": case.lane_id,
                    "matrix_id": case.matrix_id,
                    "parameters": dict(case.parameters),
                    "execution_boundary": "core_adapter",
                    "benchmark_event_stream": case_input,
                    "benchmark_input_sha256": input_sha256,
                    "replay_from_pristine_initial_state": True,
                },
            )
            outer = _strict_evidence_object(
                evidence, ["execution"], f"{case.case_id} {operation} evidence"
            )
            execution, invariants = _normalize_core_execution(
                outer["execution"],
                case_input=case_input,
                input_sha256=input_sha256,
                where=f"{case.case_id} {operation}",
            )
            executions.append(execution)
            invariant_sets.append(invariants)
            raw_records.append(
                {
                    "case_id": case.case_id,
                    "phase": "step",
                    "step_index": step_index,
                    "operation": operation,
                    "benchmark_input_sha256": input_sha256,
                    "evidence": dict(evidence),
                }
            )
        first_identity = hashlib.sha256(
            canonical_json(executions[0]).encode("utf-8")
        ).hexdigest()
        replay_identity = hashlib.sha256(
            canonical_json(executions[1]).encode("utf-8")
        ).hexdigest()
        invariant_results = [
            {
                "id": item["id"],
                "passed": bool(item["passed"] and invariant_sets[1][index]["passed"]),
            }
            for index, item in enumerate(invariant_sets[0])
        ]
        normalized = {
            "record": {
                "first_state_hash": first_identity,
                "replay_state_hash": replay_identity,
                "invariants": [item["passed"] for item in invariant_results],
                "transition_committed": executions[0]["receipt"][
                    "transition_committed"
                ],
                "effects": [item["effect_id"] for item in executions[0]["published_effects"]],
            }
        }
        collection = {
            "input": case_input,
            "input_sha256": input_sha256,
            "executions": executions,
            "execution_sha256": [first_identity, replay_identity],
            "invariants": invariant_results,
        }
        return [normalized], raw_records, collection

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections:
            return ()
        values = {
            "event_trace": [
                {
                    "case_id": item["case_id"],
                    "input": item["evidence"]["input"],
                    "input_sha256": item["evidence"]["input_sha256"],
                    "executions": item["evidence"]["executions"],
                }
                for item in case_collections
            ],
            "state_hashes": [
                {
                    "case_id": item["case_id"],
                    "execution_sha256": item["evidence"]["execution_sha256"],
                }
                for item in case_collections
            ],
            "invariant_results": [
                {
                    "case_id": item["case_id"],
                    "invariants": item["evidence"]["invariants"],
                }
                for item in case_collections
            ],
        }
        artifacts: List[Mapping[str, Any]] = []
        for artifact_id, value in values.items():
            path = context.artifact_root / f"benchmark-{artifact_id.replace('_', '-')}.json"
            write_json(path, value)
            artifacts.append(_local_artifact(artifact_id, path, context.artifact_root))
        return tuple(artifacts)


def _scheduler_performance_case_input(
    parameters: Mapping[str, Any],
) -> Tuple[List[Mapping[str, Any]], List[Optional[str]]]:
    model_count = parameters.get("runnable_models")
    urgency_mix = parameters.get("urgency_mix")
    resource_mode = parameters.get("resource_mode")
    if (
        isinstance(model_count, bool)
        or not isinstance(model_count, int)
        or model_count not in {1, 2, 8, 32, 128}
        or urgency_mix not in {"none", "mixed"}
        or resource_mode not in {"normal", "guarded", "stop_admission", "critical"}
    ):
        raise ContractError("scheduler performance CasePlan parameters are invalid")
    snapshots: List[Mapping[str, Any]] = []
    expected: List[Optional[str]] = []
    for snapshot_index in range(4):
        now_us = 1_000_000 + snapshot_index * 10_000
        candidates: List[Mapping[str, Any]] = []
        sortable: List[Tuple[Mapping[str, Any], Fraction]] = []
        for model_index in range(model_count):
            model_id = f"model-{model_index:03d}"
            candidate_id = f"candidate-{snapshot_index:02d}-{model_index:03d}"
            ledger = Fraction(
                (model_index + snapshot_index) % max(model_count, 2),
                (model_index % 3) + 1,
            )
            resource_safe = resource_mode in {"normal", "guarded"} and not (
                resource_mode == "guarded" and model_index % 4 == 3
            )
            latest_safe_start_us = (
                now_us - 100 + model_index
                if urgency_mix == "mixed" and model_index % 3 == 0
                else None
            )
            candidate = {
                "candidate_id": candidate_id,
                "model_id": model_id,
                "ledger": f"{ledger.numerator}/{ledger.denominator}",
                "capability_authorized": True,
                "resource_safe": resource_safe,
                "timing_feasible": True,
                "output_reserved": True,
                "latest_safe_start_us": latest_safe_start_us,
            }
            candidates.append(candidate)
            if resource_safe:
                sortable.append((candidate, ledger))
        urgent = [
            item
            for item in sortable
            if item[0]["latest_safe_start_us"] is not None
            and now_us >= item[0]["latest_safe_start_us"]
        ]
        if urgent:
            selected = min(
                urgent,
                key=lambda item: (
                    item[0]["latest_safe_start_us"],
                    item[1],
                    item[0]["candidate_id"],
                ),
            )[0]["candidate_id"]
        elif sortable:
            selected = min(
                sortable,
                key=lambda item: (
                    item[1],
                    item[0]["model_id"],
                    item[0]["candidate_id"],
                ),
            )[0]["candidate_id"]
        else:
            selected = None
        snapshots.append(
            {
                "snapshot_id": f"snapshot-{snapshot_index:02d}",
                "now_us": now_us,
                "resource_mode": resource_mode,
                "candidates": candidates,
            }
        )
        expected.append(selected)
    return snapshots, expected


class SchedulerPerformanceLaneRunner(EvidenceLaneRunner):
    lane_id = "scheduler-performance"

    def execute_case_steps(
        self,
        context: LaneContext,
        subject: SubjectSession,
        hello: SubjectHello,
        case: PlannedCase,
        benchmark_runtime_root: Optional[Path],
    ) -> Tuple[
        List[Mapping[str, Any]],
        List[Mapping[str, Any]],
        Optional[Mapping[str, Any]],
    ]:
        if hello.identity.kind == "fixture":
            return super().execute_case_steps(
                context, subject, hello, case, benchmark_runtime_root
            )
        if benchmark_runtime_root is not None or tuple(case.operations) != (
            "measure-release-core",
        ):
            raise ContractError("scheduler performance suite operations are not canonical")
        snapshots, expected = _scheduler_performance_case_input(case.parameters)
        input_identity = hashlib.sha256(
            canonical_json(snapshots).encode("utf-8")
        ).hexdigest()
        evidence = subject.case_step(
            case.case_id,
            1,
            case.operations[0],
            {
                "lane_id": case.lane_id,
                "matrix_id": case.matrix_id,
                "parameters": dict(case.parameters),
                "execution_boundary": "core_adapter",
                "external_inputs": dict(context.external_inputs),
                "benchmark_snapshots": snapshots,
                "benchmark_input_sha256": input_identity,
                "measurement_contract": {
                    "build_mode": "release",
                    "warmup_iterations": 100,
                    "minimum_measured_iterations": 1000,
                    "timer": "monotonic",
                    "measured_region": "scheduler_core_only",
                    "jsonl_ipc_in_measurement_region": False,
                },
            },
        )
        outer = _strict_evidence_object(
            evidence, ["record"], f"{case.case_id} scheduler performance evidence"
        )
        record = _strict_evidence_object(
            outer["record"],
            [
                "benchmark_input_sha256",
                "observed_plans",
                "decision_latency_us",
                "decision_work",
                "driver_ipc_included",
                "measurement",
            ],
            f"{case.case_id} scheduler performance record",
        )
        if record["benchmark_input_sha256"] != input_identity:
            raise ContractError(
                f"{case.case_id} measured a different Benchmark Snapshot identity"
            )
        observed = record["observed_plans"]
        if not isinstance(observed, list) or len(observed) != len(snapshots):
            raise ContractError(
                f"{case.case_id} must return one observed Plan per Benchmark Snapshot"
            )
        plan_pairs: List[Mapping[str, Any]] = []
        for index, raw in enumerate(observed):
            item = _strict_evidence_object(
                raw,
                ["snapshot_id", "candidate_id"],
                f"{case.case_id} observed_plans[{index}]",
            )
            if item["snapshot_id"] != snapshots[index]["snapshot_id"]:
                raise ContractError(
                    f"{case.case_id} observed Plan order differs from Benchmark input"
                )
            if item["candidate_id"] is not None and not isinstance(
                item["candidate_id"], str
            ):
                raise ContractError(
                    f"{case.case_id} observed candidate_id must be a string or null"
                )
            plan_pairs.append(
                {"expected": expected[index], "observed": item["candidate_id"]}
            )
        measurement = _strict_evidence_object(
            record["measurement"],
            [
                "build_mode",
                "warmup_iterations",
                "measured_iterations",
                "timer",
                "measured_region",
            ],
            f"{case.case_id} scheduler performance measurement",
        )
        latency_samples = record["decision_latency_us"]
        measured_iterations = measurement["measured_iterations"]
        if (
            measurement["build_mode"] != "release"
            or measurement["warmup_iterations"] != 100
            or isinstance(measured_iterations, bool)
            or not isinstance(measured_iterations, int)
            or measured_iterations < 1000
            or measurement["timer"] != "monotonic"
            or measurement["measured_region"] != "scheduler_core_only"
        ):
            raise ContractError(
                f"{case.case_id} did not satisfy the fixed Release Core measurement contract"
            )
        if not isinstance(latency_samples, list) or len(latency_samples) != measured_iterations:
            raise ContractError(
                f"{case.case_id} must return one latency sample per measured Core decision"
            )
        work = _strict_evidence_object(
            record["decision_work"],
            ["operations", "seconds"],
            f"{case.case_id} scheduler performance work",
        )
        if work["operations"] != measured_iterations:
            raise ContractError(
                f"{case.case_id} decision_work.operations must equal measured_iterations"
            )
        normalized = {
            "record": {
                "plan_pairs": plan_pairs,
                "decision_latency_us": latency_samples,
                "decision_work": work,
                "driver_ipc_included": record["driver_ipc_included"],
            }
        }
        raw_record = {
            "case_id": case.case_id,
            "phase": "step",
            "step_index": 1,
            "operation": case.operations[0],
            "evidence": dict(evidence),
            "benchmark_oracle": {
                "input_sha256": input_identity,
                "expected_candidate_ids": expected,
            },
        }
        collection = {
            "input_sha256": input_identity,
            "snapshots": snapshots,
            "plan_pairs": plan_pairs,
            "latency_samples_us": latency_samples,
            "decision_work": work,
            "measurement": measurement,
            "driver_ipc_included": record["driver_ipc_included"],
        }
        return [normalized], [raw_record], collection

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections:
            return ()
        values = {
            "plan_trace": [
                {
                    "case_id": item["case_id"],
                    "input_sha256": item["evidence"]["input_sha256"],
                    "plan_pairs": item["evidence"]["plan_pairs"],
                }
                for item in case_collections
            ],
            "oracle_trace": [
                {
                    "case_id": item["case_id"],
                    "input_sha256": item["evidence"]["input_sha256"],
                    "snapshots": item["evidence"]["snapshots"],
                }
                for item in case_collections
            ],
            "latency_samples": [
                {
                    "case_id": item["case_id"],
                    "samples_us": item["evidence"]["latency_samples_us"],
                }
                for item in case_collections
            ],
            "measurement_trace": [
                {
                    "case_id": item["case_id"],
                    "measurement": item["evidence"]["measurement"],
                    "decision_work": item["evidence"]["decision_work"],
                    "driver_ipc_included": item["evidence"][
                        "driver_ipc_included"
                    ],
                }
                for item in case_collections
            ],
        }
        artifacts: List[Mapping[str, Any]] = []
        for artifact_id, value in values.items():
            path = context.artifact_root / f"benchmark-{artifact_id.replace('_', '-')}.json"
            write_json(path, value)
            artifacts.append(_local_artifact(artifact_id, path, context.artifact_root))
        return tuple(artifacts)


def _strict_evidence_object(
    value: Any, fields: Sequence[str], where: str
) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError(f"{where} must contain exactly {sorted(fields)!r}")
    return value


def _positive_sequence(value: Any, where: str, *, optional: bool = False) -> Optional[int]:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{where} must be a positive integer")
    return value


def _is_subsequence(observed: Sequence[Any], complete: Sequence[Any]) -> bool:
    iterator = iter(complete)
    return all(any(candidate == item for candidate in iterator) for item in observed)


class DirectDataPlaneLaneRunner(EvidenceLaneRunner):
    """Runs implementation requests itself; the adapter only prepares and exposes traces."""

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        try:
            return super().run(context, subject, hello)
        except DataPlaneEnvironmentError as error:
            return _lane_result_with_message(
                context, "environment_unavailable", str(error)
            )

    def execute_case_steps(
        self,
        context: LaneContext,
        subject: SubjectSession,
        hello: SubjectHello,
        case: PlannedCase,
        benchmark_runtime_root: Optional[Path],
    ) -> Tuple[
        List[Mapping[str, Any]],
        List[Mapping[str, Any]],
        Optional[Mapping[str, Any]],
    ]:
        if hello.identity.kind == "fixture":
            return super().execute_case_steps(
                context, subject, hello, case, benchmark_runtime_root
            )
        if benchmark_runtime_root is not None:
            raise ContractError("direct Data Plane cases cannot receive a runtime root")
        if tuple(case.operations) != (
            "prepare-data-plane-case",
            "collect-production-trace",
        ):
            raise ContractError("direct Data Plane suite operations are not canonical")
        descriptor = DataPlaneDescriptor.parse(hello.data_plane)
        common_payload = {
            "lane_id": case.lane_id,
            "matrix_id": case.matrix_id,
            "parameters": dict(case.parameters),
            "behavior_case_ids": list(case.behavior_case_ids),
            "execution_boundary": "direct_data_plane",
            "external_inputs": dict(context.external_inputs),
        }
        prepared = subject.case_step(
            case.case_id,
            1,
            case.operations[0],
            common_payload,
        )
        if prepared != {"prepared": True}:
            raise ContractError(
                f"{case.case_id} prepare response must be exactly {{'prepared': true}}"
            )
        direct = self.drive_data_plane_case(descriptor, case)
        request_ids = self.request_ids(direct)
        production = subject.case_step(
            case.case_id,
            2,
            case.operations[1],
            {**common_payload, "request_ids": request_ids},
        )
        merged = self.merge_production_trace(case, direct, production)
        records = [
            {
                "case_id": case.case_id,
                "phase": "step",
                "step_index": 1,
                "operation": case.operations[0],
                "evidence": dict(prepared),
            },
            {
                "case_id": case.case_id,
                "phase": "benchmark_data_plane",
                "evidence": dict(direct),
            },
            {
                "case_id": case.case_id,
                "phase": "step",
                "step_index": 2,
                "operation": case.operations[1],
                "evidence": dict(production),
            },
        ]
        return [dict(prepared), dict(production)], records, merged

    def drive_data_plane_case(
        self, descriptor: DataPlaneDescriptor, case: PlannedCase
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    def request_ids(self, direct: Mapping[str, Any]) -> List[str]:
        raise NotImplementedError

    def merge_production_trace(
        self,
        case: PlannedCase,
        direct: Mapping[str, Any],
        production: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    def analyze_case(
        self,
        context: LaneContext,
        case: PlannedCase,
        step_evidence: Sequence[Mapping[str, Any]],
        close_observations: Mapping[str, Any],
        collection: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if collection is None:
            return super().analyze_case(
                context, case, step_evidence, close_observations, collection
            )
        if close_observations:
            raise ContractError(
                f"{case.case_id} observations must be empty; Benchmark owns Data Plane metrics"
            )
        record = collection.get("record")
        if not isinstance(record, dict):
            raise ContractError(f"{case.case_id} direct collection omitted its raw record")
        return analyze_lane_evidence(
            context.lane.lane_id,
            case.parameters,
            [{"record": record}],
            {},
        )


class RequestServingLifecycleLaneRunner(DirectDataPlaneLaneRunner):
    lane_id = "request-serving-lifecycle"

    def drive_data_plane_case(
        self, descriptor: DataPlaneDescriptor, case: PlannedCase
    ) -> Mapping[str, Any]:
        client_event = str(case.parameters["client_event"])
        return run_generation(
            descriptor,
            model_revision=descriptor.model_revisions["dense"],
            service_class=str(case.parameters["service_class"]),
            input_token_count=64,
            max_output_tokens=4096 if client_event == "backpressure_timeout" else 16,
            seed=20260812,
            client_event=client_event,
        )

    def request_ids(self, direct: Mapping[str, Any]) -> List[str]:
        return [str(direct["request_id"])]

    def merge_production_trace(
        self,
        case: PlannedCase,
        direct: Mapping[str, Any],
        production: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        outer = _strict_evidence_object(
            production, ["production_trace"], f"{case.case_id} production evidence"
        )
        trace = _strict_evidence_object(
            outer["production_trace"],
            [
                "request_id",
                "lifecycle",
                "outputs",
                "cancellation_commit_sequence",
                "receipt_commit_sequence",
                "disconnect_observed",
                "backpressure_timeout_observed",
                "terminal_status_emitted",
            ],
            f"{case.case_id} production trace",
        )
        if trace["request_id"] != direct["request_id"]:
            raise ContractError(f"{case.case_id} production trace request ID differs")
        lifecycle = trace["lifecycle"]
        if not isinstance(lifecycle, list) or any(
            not isinstance(state, str) for state in lifecycle
        ):
            raise ContractError(f"{case.case_id} production lifecycle must be a string array")
        if not _is_subsequence(direct["lifecycle"], lifecycle):
            raise ContractError(
                f"{case.case_id} client lifecycle is not present in the production trace"
            )
        outputs = trace["outputs"]
        if not isinstance(outputs, list):
            raise ContractError(f"{case.case_id} production outputs must be an array")
        normalized_outputs: List[Mapping[str, Any]] = []
        for index, raw in enumerate(outputs):
            item = _strict_evidence_object(
                raw,
                ["publication_id", "sequence", "reserved"],
                f"{case.case_id} production outputs[{index}]",
            )
            if (
                not isinstance(item["publication_id"], str)
                or isinstance(item["sequence"], bool)
                or not isinstance(item["sequence"], int)
                or item["sequence"] < 0
                or not isinstance(item["reserved"], bool)
            ):
                raise ContractError(f"{case.case_id} production output fields are invalid")
            normalized_outputs.append(dict(item))
        client_outputs = [
            {
                "publication_id": item["publication_id"],
                "sequence": item["sequence"],
                "reserved": item["reserved"],
            }
            for item in direct["outputs"]
        ]
        if not _is_subsequence(client_outputs, normalized_outputs):
            raise ContractError(
                f"{case.case_id} client outputs are not present in the production trace"
            )
        booleans = (
            "disconnect_observed",
            "backpressure_timeout_observed",
            "terminal_status_emitted",
        )
        if any(not isinstance(trace[field], bool) for field in booleans):
            raise ContractError(f"{case.case_id} production event flags must be boolean")
        record = {
            "lifecycle": list(lifecycle),
            "outputs": normalized_outputs,
            "client_event": case.parameters["client_event"],
            "acceptance_reserved": bool(direct["accepted"]["reservation_created"]),
            "acceptance_backend_handle": bool(
                direct["accepted"]["backend_handle_created"]
            ),
            "cancellation_commit_sequence": _positive_sequence(
                trace["cancellation_commit_sequence"],
                f"{case.case_id} cancellation_commit_sequence",
                optional=True,
            ),
            "receipt_commit_sequence": _positive_sequence(
                trace["receipt_commit_sequence"],
                f"{case.case_id} receipt_commit_sequence",
                optional=True,
            ),
            "disconnect_observed": trace["disconnect_observed"],
            "backpressure_timeout_observed": trace[
                "backpressure_timeout_observed"
            ],
            "terminal_status_emitted": trace["terminal_status_emitted"],
        }
        return {
            "mode": "direct_data_plane",
            "direct": dict(direct),
            "production_trace": dict(trace),
            "record": record,
        }

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections or any(
            item["evidence"].get("mode") != "direct_data_plane"
            for item in case_collections
        ):
            return ()
        evidence = [dict(item) for item in case_collections]
        values = {
            "request_trace": evidence,
            "status_trace": [
                {
                    "case_id": item["case_id"],
                    "lifecycle": item["evidence"]["record"]["lifecycle"],
                    "terminal_status_emitted": item["evidence"]["record"][
                        "terminal_status_emitted"
                    ],
                }
                for item in evidence
            ],
            "output_trace": [
                {
                    "case_id": item["case_id"],
                    "outputs": item["evidence"]["record"]["outputs"],
                }
                for item in evidence
            ],
            "capacity_trace": [
                {
                    "case_id": item["case_id"],
                    "acceptance_reserved": item["evidence"]["record"][
                        "acceptance_reserved"
                    ],
                    "acceptance_backend_handle": item["evidence"]["record"][
                        "acceptance_backend_handle"
                    ],
                    "outputs": item["evidence"]["record"]["outputs"],
                }
                for item in evidence
            ],
        }
        artifacts: List[Mapping[str, Any]] = []
        for artifact_id, value in values.items():
            path = context.artifact_root / f"{artifact_id.replace('_', '-')}.json"
            write_json(path, value)
            artifacts.append(_local_artifact(artifact_id, path, context.artifact_root))
        return tuple(artifacts)


class MlxNativeCorrectnessLaneRunner(EvidenceLaneRunner):
    lane_id = "mlx-native-correctness"

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        if hello.identity.kind == "fixture":
            return super().run(context, subject, hello)
        required_external = {"mlx-python", "dense-model", "moe-model"}
        if not required_external.issubset(context.external_inputs):
            missing = sorted(required_external - set(context.external_inputs))
            return _lane_result_with_message(
                context,
                "environment_unavailable",
                f"MLX oracle inputs are unavailable: {missing!r}",
            )
        benchmark_root = Path(__file__).resolve().parent.parent
        oracle = benchmark_root / "oracles" / "mlx" / "reference_oracle.py"
        python = Path(str(context.external_inputs["mlx-python"]["path"]))
        raw_records: List[Mapping[str, Any]] = []
        mismatch_values = {
            "output_mismatch_count": 0,
            "complete_logits_mismatch_count": 0,
            "kv_state_mismatch_count": 0,
        }
        owner_violations: List[Any] = []
        owner_records: List[Mapping[str, Any]] = []
        candidate_artifacts: List[Mapping[str, Any]] = []
        comparisons: List[Mapping[str, Any]] = []
        executed = 0
        for case in context.plan.cases:
            oracle_dir = context.artifact_root / "oracle" / f"{case.ordinal:04d}"
            model_architecture = str(case.parameters["model_architecture"])
            model_key = f"{model_architecture}-model"
            model_path = Path(str(context.external_inputs[model_key]["path"]))
            phase = "decode" if case.matrix_id == "decode" else "prefill"
            batch = int(case.parameters.get("batch_size", 1))
            shape = int(
                case.parameters.get(
                    "context_tokens", case.parameters.get("prompt_tokens", 0)
                )
            )
            command = [
                str(python),
                "-B",
                str(oracle),
                "--model",
                str(model_path),
                "--model-architecture",
                model_architecture,
                "--phase",
                phase,
                "--batch",
                str(batch),
                "--shape",
                str(shape),
                "--output",
                str(oracle_dir),
            ]
            completed = subprocess.run(
                command,
                cwd=str(benchmark_root),
                capture_output=True,
                text=True,
            )
            raw_records.append(
                {
                    "case_id": case.case_id,
                    "phase": "mlx_oracle",
                    "command": command,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-4096:],
                    "stderr": completed.stderr[-4096:],
                }
            )
            if completed.returncode != 0:
                return _lane_result_with_message(
                    context,
                    "environment_unavailable",
                    f"pinned MLX oracle failed for {case.case_id}: "
                    f"{completed.stderr.strip()!r}",
                    executed=executed,
                    raw_records=raw_records,
                )
            oracle_manifest = json.loads(
                (oracle_dir / "manifest.json").read_text(encoding="utf-8")
            )
            if oracle_manifest.get("qualification_eligible") is not True:
                raise ContractError(
                    f"MLX oracle evidence for {case.case_id} is diagnostic-only"
                )
            case_directory = f"cases/{case.ordinal:04d}"
            (context.artifact_root / case_directory).mkdir(parents=True, exist_ok=False)
            context.case_start_monitor.mark_case_started(context.lane.lane_id)
            open_status = subject.case_open(
                context.run_id, case, context.artifact_root, case_directory
            )
            if open_status != "ready":
                return _lane_result_with_message(
                    context,
                    open_status,
                    f"subject returned {open_status} from {case.case_id}",
                    executed=executed,
                    raw_records=raw_records,
                )
            for step_index, operation in enumerate(case.operations, start=1):
                evidence = subject.case_step(
                    case.case_id,
                    step_index,
                    operation,
                    {
                        "matrix_id": case.matrix_id,
                        "parameters": dict(case.parameters),
                        "seed": 20260812,
                        "model_artifact": dict(context.external_inputs[model_key]),
                        "reference_lock_sha256": sha256_file(
                            benchmark_root / "oracles" / "mlx" / "reference-lock-v1.json"
                        ),
                    },
                )
                raw_records.append(
                    {
                        "case_id": case.case_id,
                        "phase": "subject_step",
                        "step_index": step_index,
                        "operation": operation,
                        "evidence": dict(evidence),
                    }
                )
            observations, descriptors = subject.case_close(case.case_id)
            if observations:
                raise ContractError(
                    f"{case.case_id} implementation observations must be empty; "
                    "parity and owner violations are computed by the benchmark"
                )
            verified = [
                validate_subject_artifact(descriptor, context.artifact_root)
                for descriptor in descriptors
            ]
            by_id = {item["id"]: item for item in verified}
            expected_ids = {
                "candidate_output",
                "candidate_logits",
                "candidate_kv",
                "owner_thread_trace",
            }
            if set(by_id) != expected_ids or len(verified) != len(expected_ids):
                raise ContractError(
                    f"{case.case_id} candidate artifacts must be exactly "
                    f"{sorted(expected_ids)!r}"
                )
            candidate_artifacts.extend(verified)
            trace_path = context.artifact_root / by_id["owner_thread_trace"]["path"]
            trace = _read_strict_jsonl(
                trace_path,
                (
                    "operation_id",
                    "owner_thread_id",
                    "execution_thread_id",
                    "cross_thread_attempt",
                    "accepted",
                ),
                f"owner thread trace for {case.case_id}",
            )
            cross_thread_attempt_seen = False
            case_owner_violations = 0
            for record in trace:
                if not isinstance(record["operation_id"], str) or not record["operation_id"]:
                    raise ContractError(
                        f"owner thread trace operation ID is invalid for {case.case_id}"
                    )
                for field in ("owner_thread_id", "execution_thread_id"):
                    if isinstance(record[field], bool) or not isinstance(record[field], int):
                        raise ContractError(
                            f"owner thread trace {field} is invalid for {case.case_id}"
                        )
                if not isinstance(record["cross_thread_attempt"], bool) or not isinstance(
                    record["accepted"], bool
                ):
                    raise ContractError(
                        f"owner thread trace flags are invalid for {case.case_id}"
                    )
                cross_thread_attempt_seen = (
                    cross_thread_attempt_seen or record["cross_thread_attempt"]
                )
                if record["cross_thread_attempt"]:
                    case_owner_violations += int(record["accepted"])
                else:
                    case_owner_violations += int(
                        not record["accepted"]
                        or record["execution_thread_id"] != record["owner_thread_id"]
                    )
                owner_records.append({"case_id": case.case_id, **record})
            if not cross_thread_attempt_seen:
                raise ContractError(
                    f"owner thread trace omits a rejected cross-thread attempt for {case.case_id}"
                )
            owner_violations.append(case_owner_violations)
            paths = {
                "output": oracle_dir / "output-tokens.i32",
                "logits": oracle_dir / "complete-logits.bin",
                "kv": oracle_dir / "layer-kv-normalized.bin",
            }
            candidate_ids = {
                "output": "candidate_output",
                "logits": "candidate_logits",
                "kv": "candidate_kv",
            }
            record: Dict[str, Any] = {"case_id": case.case_id}
            for name, oracle_path in paths.items():
                candidate = by_id[candidate_ids[name]]
                oracle_hash = sha256_file(oracle_path)
                matches = candidate["sha256"] == oracle_hash
                metric = {
                    "output": "output_mismatch_count",
                    "logits": "complete_logits_mismatch_count",
                    "kv": "kv_state_mismatch_count",
                }[name]
                mismatch_values[metric] += int(not matches)
                record[name] = {
                    "oracle_sha256": oracle_hash,
                    "candidate_sha256": candidate["sha256"],
                    "matches": matches,
                }
            comparisons.append(record)
            raw_records.append(record)
            executed += 1
        output_hashes = context.artifact_root / "output-hashes.json"
        logits_hashes = context.artifact_root / "logits-hashes.json"
        kv_hashes = context.artifact_root / "kv-hashes.json"
        owner_trace = context.artifact_root / "owner-thread-trace.jsonl"
        native_oracle = context.artifact_root / "native-oracle.json"
        write_json(
            output_hashes,
            [
                {"case_id": item["case_id"], "kind": "output", **item["output"]}
                for item in comparisons
            ],
        )
        write_json(
            logits_hashes,
            [
                {"case_id": item["case_id"], "kind": "logits", **item["logits"]}
                for item in comparisons
            ],
        )
        write_json(
            kv_hashes,
            [
                {"case_id": item["case_id"], "kind": "kv", **item["kv"]}
                for item in comparisons
            ],
        )
        write_jsonl(owner_trace, owner_records)
        write_json(
            native_oracle,
            {
                "oracle": str(oracle),
                "reference_lock": str(
                    benchmark_root / "oracles" / "mlx" / "reference-lock-v1.json"
                ),
                "case_count": executed,
                "decode_kv_policy": "prefill_generated_nonzero_kv",
            },
        )
        artifacts = candidate_artifacts + [
            _local_artifact("native_oracle", native_oracle, context.artifact_root),
            _local_artifact("output_hashes", output_hashes, context.artifact_root),
            _local_artifact("logits_hashes", logits_hashes, context.artifact_root),
            _local_artifact("kv_hashes", kv_hashes, context.artifact_root),
            _local_artifact("owner_thread_trace", owner_trace, context.artifact_root),
        ]
        metrics = {
            **mismatch_values,
            "owner_thread_violation_count": reduce_observations(
                context.suite.metric_recipe("owner_thread_violation_count"),
                owner_violations,
            ),
        }
        gates = evaluate_gates(context.lane, metrics, context.frozen_thresholds)
        failed = [item for item in gates if item["status"] != "passed"]
        return LaneResult(
            lane_id=context.lane.lane_id,
            status="passed" if not failed else "gate_failed",
            case_count=len(context.plan.cases),
            executed_case_count=executed,
            metrics=metrics,
            gates=gates,
            failures=tuple(
                {
                    "kind": "gate_failed",
                    "gate_id": item["gate_id"],
                    "metric": item["metric"],
                    "expected": item["expected"],
                    "observed": item["observed"],
                }
                for item in failed
            ),
            artifacts=tuple(artifacts),
            raw_records=tuple(raw_records),
        )


class BoundedTurnAndFfiLaneRunner(EvidenceLaneRunner):
    lane_id = "bounded-turn-and-ffi"

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        if hello.identity.kind == "fixture":
            return super().run(context, subject, hello)
        required = {"cpp-direct-build", "dense-model", "moe-model", "mlx-c-source"}
        if not required.issubset(context.external_inputs):
            return _lane_result_with_message(
                context,
                "environment_unavailable",
                "C++ Direct inputs are unavailable: "
                f"{sorted(required - set(context.external_inputs))!r}",
            )
        benchmark_root = Path(__file__).resolve().parent.parent
        bundle = _load_cpp_direct_bundle(
            context.external_inputs["cpp-direct-build"], benchmark_root
        )
        baselines: Dict[Tuple[str, str, int, int], Mapping[str, Any]] = {}
        raw_records: List[Mapping[str, Any]] = []
        comparisons: List[Mapping[str, Any]] = []
        latency_records: List[Mapping[str, Any]] = []
        receipt_records: List[Mapping[str, Any]] = []
        cleanup_records: List[Mapping[str, Any]] = []
        candidate_artifacts: List[Mapping[str, Any]] = []
        turn_service_samples: List[float] = []
        regression_samples: List[float] = []
        mismatch_count = 0
        unclean_count = 0
        executed = 0

        for case in context.plan.cases:
            architecture = str(case.parameters["model_architecture"])
            phase = "decode" if case.matrix_id == "decode-boundary" else "prefill"
            batch = int(case.parameters.get("batch_size", 1))
            shape = int(
                case.parameters.get(
                    "context_tokens", case.parameters.get("prompt_tokens", 0)
                )
            )
            key = (architecture, phase, batch, shape)
            boundary = case.parameters["boundary"]
            model = bundle["models"][architecture]

            if boundary == "cpp_direct_oracle":
                oracle_root = context.artifact_root / "cpp-direct" / f"{case.ordinal:04d}"
                oracle_root.mkdir(parents=True)
                output_prefix = oracle_root / "turn"
                command = [
                    str(bundle["binary"]),
                    str(model["graph"]),
                    str(output_prefix),
                    phase,
                    str(batch),
                    str(shape),
                    str(model["layers"]),
                    str(model["kv_heads"]),
                    str(model["head_dim"]),
                    str(model["vocab_size"]),
                    str(bundle["warmup"]),
                    str(bundle["iterations"]),
                    str(bundle["seed"]),
                ]
                try:
                    completed = subprocess.run(
                        command,
                        cwd=str(bundle["root"]),
                        capture_output=True,
                        text=True,
                        timeout=3600,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    return _lane_result_with_message(
                        context,
                        "environment_unavailable",
                        f"C++ Direct oracle could not run for {case.case_id}: {error}",
                        executed=executed,
                        raw_records=raw_records,
                    )
                command_record = {
                    "case_id": case.case_id,
                    "phase": "cpp_direct_oracle",
                    "command": command,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-4096:],
                    "stderr": completed.stderr[-4096:],
                }
                raw_records.append(command_record)
                if completed.returncode != 0:
                    return _lane_result_with_message(
                        context,
                        "environment_unavailable",
                        f"C++ Direct oracle failed for {case.case_id}: "
                        f"{completed.stderr.strip()!r}",
                        executed=executed,
                        raw_records=raw_records,
                    )
                csv_path = Path(str(output_prefix) + ".csv")
                logits_path = Path(str(output_prefix) + ".logits.f32")
                kv_path = Path(str(output_prefix) + ".kv.f32")
                for path in (csv_path, logits_path, kv_path):
                    if not path.is_file() or path.stat().st_size == 0:
                        raise ContractError(
                            f"C++ Direct oracle omitted non-empty output {path}"
                        )
                samples = _read_latency_csv(
                    csv_path,
                    ("iteration", "wall_us"),
                    f"C++ Direct latency for {case.case_id}",
                )
                if len(samples) != bundle["iterations"]:
                    raise ContractError(
                        f"C++ Direct latency sample count differs for {case.case_id}"
                    )
                baseline = {
                    "case_id": case.case_id,
                    "logits_sha256": sha256_file(logits_path),
                    "kv_sha256": sha256_file(kv_path),
                    "wall_us": [item["wall_us"] for item in samples],
                }
                if key in baselines:
                    raise ContractError(f"duplicate C++ Direct baseline for {key!r}")
                baselines[key] = baseline
                latency_records.append(
                    {
                        "case_id": case.case_id,
                        "boundary": "cpp_direct_oracle",
                        "wall_us": baseline["wall_us"],
                    }
                )
                executed += 1
                continue

            if boundary != "implementation_candidate":
                raise ContractError(f"unknown native boundary {boundary!r}")
            baseline = baselines.get(key)
            if baseline is None:
                raise ContractError(f"candidate boundary has no prior C++ Direct pair for {key!r}")
            case_directory = f"cases/{case.ordinal:04d}"
            (context.artifact_root / case_directory).mkdir(parents=True, exist_ok=False)
            context.case_start_monitor.mark_case_started(context.lane.lane_id)
            open_status = subject.case_open(
                context.run_id, case, context.artifact_root, case_directory
            )
            if open_status != "ready":
                return _lane_result_with_message(
                    context,
                    open_status,
                    f"subject returned {open_status} from {case.case_id}",
                    executed=executed,
                    raw_records=raw_records,
                )
            for step_index, operation in enumerate(case.operations, start=1):
                evidence = subject.case_step(
                    case.case_id,
                    step_index,
                    operation,
                    {
                        "matrix_id": case.matrix_id,
                        "parameters": dict(case.parameters),
                        "seed": bundle["seed"],
                        "model_artifact": dict(
                            context.external_inputs[f"{architecture}-model"]
                        ),
                        "cpp_direct_bundle_sha256": context.external_inputs[
                            "cpp-direct-build"
                        ]["sha256"],
                    },
                )
                raw_records.append(
                    {
                        "case_id": case.case_id,
                        "phase": "candidate_step",
                        "step_index": step_index,
                        "operation": operation,
                        "evidence": dict(evidence),
                    }
                )
            observations, descriptors = subject.case_close(case.case_id)
            if observations:
                raise ContractError(
                    f"{case.case_id} candidate observations must be empty; raw files are judged"
                )
            verified = [
                validate_subject_artifact(descriptor, context.artifact_root)
                for descriptor in descriptors
            ]
            by_id = {item["id"]: item for item in verified}
            expected_ids = {
                "candidate_logits",
                "candidate_kv",
                "candidate_latency",
                "turn_receipt",
                "candidate_cleanup",
            }
            if set(by_id) != expected_ids or len(verified) != len(expected_ids):
                raise ContractError(
                    f"{case.case_id} candidate artifacts must be exactly "
                    f"{sorted(expected_ids)!r}"
                )
            candidate_artifacts.extend(verified)
            candidate_latency_path = context.artifact_root / by_id["candidate_latency"]["path"]
            candidate_samples = _read_latency_csv(
                candidate_latency_path,
                ("iteration", "wall_us", "engine_service_us"),
                f"candidate latency for {case.case_id}",
            )
            if len(candidate_samples) != len(baseline["wall_us"]):
                raise ContractError(
                    f"candidate and C++ Direct sample counts differ for {case.case_id}"
                )
            candidate_wall = [item["wall_us"] for item in candidate_samples]
            engine_service = [item["engine_service_us"] for item in candidate_samples]
            turn_service_samples.extend(engine_service)
            for observed, reference in zip(candidate_wall, baseline["wall_us"]):
                regression_samples.append((observed - reference) / reference * 100.0)

            receipt_path = context.artifact_root / by_id["turn_receipt"]["path"]
            receipt = _read_strict_json(
                receipt_path,
                ("schema_version", "case_id", "outcomes"),
                f"Turn Receipt for {case.case_id}",
            )
            if (
                receipt["schema_version"]
                != "turnvector.benchmark.turn-receipt-evidence.v1"
                or receipt["case_id"] != case.case_id
                or not isinstance(receipt["outcomes"], list)
                or not receipt["outcomes"]
            ):
                raise ContractError(f"Turn Receipt identity is invalid for {case.case_id}")
            terminals = set()
            for outcome in receipt["outcomes"]:
                if not isinstance(outcome, dict) or set(outcome) != {"terminal", "bounded"}:
                    raise ContractError(f"Turn Receipt outcome is invalid for {case.case_id}")
                if outcome["terminal"] not in {"completed", "cancelled", "failed"}:
                    raise ContractError(f"Turn Receipt terminal is invalid for {case.case_id}")
                if not isinstance(outcome["bounded"], bool) or not outcome["bounded"]:
                    raise ContractError(f"Turn Receipt is unbounded for {case.case_id}")
                terminals.add(outcome["terminal"])
            if not {"completed", "cancelled"}.issubset(terminals):
                raise ContractError(
                    f"{case.case_id} must exercise completed and cancelled native outcomes"
                )
            receipt_records.append(dict(receipt))

            cleanup_path = context.artifact_root / by_id["candidate_cleanup"]["path"]
            cleanup = _read_strict_json(
                cleanup_path,
                ("schema_version", "case_id", "outcomes"),
                f"cleanup trace for {case.case_id}",
            )
            if (
                cleanup["schema_version"]
                != "turnvector.benchmark.native-cleanup-evidence.v1"
                or cleanup["case_id"] != case.case_id
                or not isinstance(cleanup["outcomes"], list)
                or not cleanup["outcomes"]
            ):
                raise ContractError(f"cleanup trace identity is invalid for {case.case_id}")
            cleanup_terminals = set()
            for outcome in cleanup["outcomes"]:
                if not isinstance(outcome, dict) or set(outcome) != {"terminal", "cleaned"}:
                    raise ContractError(f"cleanup outcome is invalid for {case.case_id}")
                if outcome["terminal"] not in {"completed", "cancelled", "failed"}:
                    raise ContractError(f"cleanup terminal is invalid for {case.case_id}")
                if not isinstance(outcome["cleaned"], bool):
                    raise ContractError(f"cleanup state is invalid for {case.case_id}")
                cleanup_terminals.add(outcome["terminal"])
                unclean_count += int(not outcome["cleaned"])
            if "cancelled" not in cleanup_terminals:
                raise ContractError(f"cleanup trace omits cancellation for {case.case_id}")
            cleanup_records.append(dict(cleanup))

            comparison = {
                "case_id": case.case_id,
                "paired_oracle_case_id": baseline["case_id"],
                "logits": {
                    "oracle_sha256": baseline["logits_sha256"],
                    "candidate_sha256": by_id["candidate_logits"]["sha256"],
                },
                "kv": {
                    "oracle_sha256": baseline["kv_sha256"],
                    "candidate_sha256": by_id["candidate_kv"]["sha256"],
                },
            }
            for kind in ("logits", "kv"):
                comparison[kind]["matches"] = (
                    comparison[kind]["oracle_sha256"]
                    == comparison[kind]["candidate_sha256"]
                )
                mismatch_count += int(not comparison[kind]["matches"])
            comparisons.append(comparison)
            latency_records.append(
                {
                    "case_id": case.case_id,
                    "boundary": "implementation_candidate",
                    "wall_us": candidate_wall,
                    "engine_service_us": engine_service,
                    "paired_oracle_case_id": baseline["case_id"],
                }
            )
            raw_records.append(
                {
                    "case_id": case.case_id,
                    "phase": "candidate_comparison",
                    "comparison": comparison,
                }
            )
            executed += 1

        if executed != len(context.plan.cases) or len(baselines) * 2 != executed:
            raise ContractError("C++ Direct/candidate case pairing is incomplete")
        native_oracle_path = context.artifact_root / "native-oracle.json"
        output_hashes_path = context.artifact_root / "output-hashes.json"
        latency_path = context.artifact_root / "latency-samples.json"
        receipts_path = context.artifact_root / "turn-receipts.json"
        cleanup_path = context.artifact_root / "cleanup-trace.json"
        write_json(
            native_oracle_path,
            {
                "schema_version": CPP_DIRECT_BUNDLE_SCHEMA,
                "bundle_manifest": str(bundle["manifest"]),
                "bundle_sha256": context.external_inputs["cpp-direct-build"]["sha256"],
                "seed": bundle["seed"],
                "baseline_case_count": len(baselines),
                "measurement_region": "cpp-direct-no-python",
                "decode_kv_policy": "prefill-generated-nonzero-kv",
            },
        )
        write_json(output_hashes_path, comparisons)
        write_json(latency_path, latency_records)
        write_json(receipts_path, receipt_records)
        write_json(cleanup_path, cleanup_records)
        artifacts = candidate_artifacts + [
            _local_artifact("native_oracle", native_oracle_path, context.artifact_root),
            _local_artifact("output_hashes", output_hashes_path, context.artifact_root),
            _local_artifact("latency_samples", latency_path, context.artifact_root),
            _local_artifact("turn_receipts", receipts_path, context.artifact_root),
            _local_artifact("cleanup_trace", cleanup_path, context.artifact_root),
        ]
        metrics = {
            "turn_service_p99_us": _percentile(turn_service_samples, 0.99),
            "ffi_output_mismatch_count": mismatch_count,
            "ffi_latency_regression_p95_percent": _percentile(
                regression_samples, 0.95
            ),
            "unclean_native_outcome_count": unclean_count,
        }
        gates = evaluate_gates(context.lane, metrics, context.frozen_thresholds)
        failed = [item for item in gates if item["status"] != "passed"]
        return LaneResult(
            lane_id=context.lane.lane_id,
            status="passed" if not failed else "gate_failed",
            case_count=len(context.plan.cases),
            executed_case_count=executed,
            metrics=metrics,
            gates=gates,
            failures=tuple(
                {
                    "kind": "gate_failed",
                    "gate_id": item["gate_id"],
                    "metric": item["metric"],
                    "expected": item["expected"],
                    "observed": item["observed"],
                }
                for item in failed
            ),
            artifacts=tuple(artifacts),
            raw_records=tuple(raw_records),
        )


class ResidencyAndMemoryGovernorLaneRunner(EvidenceLaneRunner):
    lane_id = "residency-and-memory-governor"

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        try:
            return super().run(context, subject, hello)
        except DataPlaneEnvironmentError as error:
            return _lane_result_with_message(
                context, "environment_unavailable", str(error)
            )

    def begin_case_collection(
        self, context: LaneContext, hello: SubjectHello, case: PlannedCase
    ) -> Any:
        del context, case
        if hello.identity.kind == "fixture":
            return None
        descriptor = DataPlaneDescriptor.parse(hello.data_plane)
        sampler = ProcessMemorySampler(descriptor.process_ids)
        sampler.start()
        return sampler

    def end_case_collection(self, collector: Any) -> Optional[Mapping[str, Any]]:
        if collector is None:
            return None
        return collector.stop().as_dict()

    def analyze_case(
        self,
        context: LaneContext,
        case: PlannedCase,
        step_evidence: Sequence[Mapping[str, Any]],
        close_observations: Mapping[str, Any],
        collection: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if collection is None:
            return super().analyze_case(
                context, case, step_evidence, close_observations, collection
            )
        samples = collection.get("process_samples")
        if not isinstance(samples, list) or not samples:
            raise ContractError("Benchmark process sampler captured no footprint samples")
        normalized = json.loads(json.dumps(step_evidence))
        records = [item["record"] for item in normalized if set(item) == {"record"}]
        if len(records) != 1:
            raise ContractError("memory lane did not emit one normalized Governor record")
        records[0]["process_samples_bytes"] = [
            item["total_rss_bytes"] for item in samples
        ]
        return analyze_lane_evidence(
            context.lane.lane_id,
            case.parameters,
            normalized,
            close_observations,
        )

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections:
            return ()
        path = context.artifact_root / "benchmark-memory-samples.json"
        write_json(
            path,
            {
                "schema_version": "turnvector.benchmark.memory-samples.v1",
                "cases": [dict(item) for item in case_collections],
            },
        )
        return (_local_artifact("memory_samples", path, context.artifact_root),)


class CrossModelServingLaneRunner(DirectDataPlaneLaneRunner):
    lane_id = "cross-model-serving"

    def drive_data_plane_case(
        self, descriptor: DataPlaneDescriptor, case: PlannedCase
    ) -> Mapping[str, Any]:
        return run_cross_model_case(descriptor, case.parameters)

    def request_ids(self, direct: Mapping[str, Any]) -> List[str]:
        return [
            str(request["request_id"])
            for request in [*direct["baselines"], *direct["concurrent"]]
        ]

    def merge_production_trace(
        self,
        case: PlannedCase,
        direct: Mapping[str, Any],
        production: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        outer = _strict_evidence_object(
            production, ["production_trace"], f"{case.case_id} production evidence"
        )
        trace = _strict_evidence_object(
            outer["production_trace"],
            ["receipts"],
            f"{case.case_id} production trace",
        )
        receipts = trace["receipts"]
        if not isinstance(receipts, list) or not receipts:
            raise ContractError(f"{case.case_id} production receipts must be non-empty")
        normalized_receipts: List[Mapping[str, Any]] = []
        for index, raw in enumerate(receipts):
            item = _strict_evidence_object(
                raw,
                ["model", "engine_service_us", "weight"],
                f"{case.case_id} receipts[{index}]",
            )
            if item["model"] not in {"alpha", "beta"}:
                raise ContractError(f"{case.case_id} receipt model is invalid")
            service = _number(
                item["engine_service_us"],
                f"{case.case_id} receipts[{index}].engine_service_us",
            )
            weight = _number(
                item["weight"], f"{case.case_id} receipts[{index}].weight"
            )
            if service <= 0 or weight <= 0:
                raise ContractError(f"{case.case_id} receipt values must be positive")
            normalized_receipts.append(
                {
                    "model": item["model"],
                    "engine_service_us": service,
                    "weight": weight,
                }
            )
        record = {
            "progress_us": list(direct["progress_us"]),
            "timing_us": list(direct["timing_us"]),
            "receipts": normalized_receipts,
            "latency_samples": [dict(item) for item in direct["latency_samples"]],
            "throughput": dict(direct["throughput"]),
            "outputs": [dict(item) for item in direct["outputs"]],
        }
        return {
            "mode": "direct_data_plane",
            "direct": dict(direct),
            "production_trace": {"receipts": normalized_receipts},
            "record": record,
        }

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections or any(
            item["evidence"].get("mode") != "direct_data_plane"
            for item in case_collections
        ):
            return ()
        evidence = [dict(item) for item in case_collections]
        values = {
            "workload": [
                {"case_id": item["case_id"], "parameters": item["parameters"]}
                for item in evidence
            ],
            "serving_trace": [
                {
                    "case_id": item["case_id"],
                    "progress_us": item["evidence"]["record"]["progress_us"],
                    "timing_us": item["evidence"]["record"]["timing_us"],
                    "protocol_traces": [
                        request["protocol_trace"]
                        for request in item["evidence"]["direct"]["concurrent"]
                    ],
                }
                for item in evidence
            ],
            "turn_receipts": [
                {
                    "case_id": item["case_id"],
                    "receipts": item["evidence"]["record"]["receipts"],
                }
                for item in evidence
            ],
            "latency_samples": [
                {
                    "case_id": item["case_id"],
                    "latency_samples": item["evidence"]["record"][
                        "latency_samples"
                    ],
                    "throughput": item["evidence"]["record"]["throughput"],
                }
                for item in evidence
            ],
            "output_hashes": [
                {
                    "case_id": item["case_id"],
                    "pairs": [
                        {
                            "expected_sha256": hashlib.sha256(
                                canonical_json(pair["expected"]).encode("utf-8")
                            ).hexdigest(),
                            "observed_sha256": hashlib.sha256(
                                canonical_json(pair["observed"]).encode("utf-8")
                            ).hexdigest(),
                        }
                        for pair in item["evidence"]["record"]["outputs"]
                    ],
                }
                for item in evidence
            ],
        }
        artifacts: List[Mapping[str, Any]] = []
        for artifact_id, value in values.items():
            path = context.artifact_root / f"{artifact_id.replace('_', '-')}.json"
            write_json(path, value)
            artifacts.append(_local_artifact(artifact_id, path, context.artifact_root))
        return tuple(artifacts)


class ObservabilityQualificationLaneRunner(EvidenceLaneRunner):
    lane_id = "observability-qualification"

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        try:
            return super().run(context, subject, hello)
        except (DataPlaneEnvironmentError, RuntimeError) as error:
            return _lane_result_with_message(
                context, "environment_unavailable", str(error)
            )

    def begin_case_collection(
        self, context: LaneContext, hello: SubjectHello, case: PlannedCase
    ) -> Any:
        if hello.identity.kind == "fixture":
            return None
        xctrace = context.external_inputs.get("xctrace")
        if xctrace is None or xctrace.get("kind") != "file":
            raise DataPlaneEnvironmentError(
                "observability qualification requires a hash-bound xctrace executable"
            )
        descriptor = DataPlaneDescriptor.parse(hello.data_plane)
        collector = XctraceCollector(
            Path(str(xctrace["path"])),
            descriptor.process_ids[0],
            context.artifact_root / "xctrace" / f"{case.ordinal:04d}",
        )
        collector.start()
        return collector

    def end_case_collection(self, collector: Any) -> Optional[Mapping[str, Any]]:
        if collector is None:
            return None
        return collector.stop()

    def analyze_case(
        self,
        context: LaneContext,
        case: PlannedCase,
        step_evidence: Sequence[Mapping[str, Any]],
        close_observations: Mapping[str, Any],
        collection: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if collection is None:
            return super().analyze_case(
                context, case, step_evidence, close_observations, collection
            )
        if collection.get("capture_present") is not True:
            raise ContractError("Benchmark xctrace collector did not retain a capture")
        normalized = json.loads(json.dumps(step_evidence))
        records = [item["record"] for item in normalized if set(item) == {"record"}]
        if len(records) != 1 or not isinstance(records[0], dict):
            raise ContractError("observability lane must emit one raw telemetry record")
        records[0]["instruments_capture_present"] = True
        return analyze_lane_evidence(
            context.lane.lane_id,
            case.parameters,
            normalized,
            close_observations,
        )

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections:
            return ()
        captures: List[Mapping[str, Any]] = []
        for item in case_collections:
            evidence = item["evidence"]
            if evidence.get("capture_present") is not True:
                return ()
            archive = Path(str(evidence["trace_archive_path"])).resolve()
            toc = Path(str(evidence["toc_path"])).resolve()
            for path in (archive, toc):
                try:
                    path.relative_to(context.artifact_root.resolve())
                except ValueError as error:
                    raise ContractError("xctrace evidence escaped the Benchmark artifact root") from error
                if not path.is_file():
                    raise ContractError("xctrace evidence disappeared before report generation")
            if (
                sha256_file(archive) != evidence["trace_archive_sha256"]
                or sha256_file(toc) != evidence["toc_sha256"]
            ):
                raise ContractError("xctrace evidence changed before report generation")
            captures.append(
                {
                    "case_id": item["case_id"],
                    "attach_pid": evidence["attach_pid"],
                    "trace_archive": {
                        "path": archive.relative_to(context.artifact_root).as_posix(),
                        "size": archive.stat().st_size,
                        "sha256": evidence["trace_archive_sha256"],
                    },
                    "table_of_contents": {
                        "path": toc.relative_to(context.artifact_root).as_posix(),
                        "size": toc.stat().st_size,
                        "sha256": evidence["toc_sha256"],
                    },
                }
            )
        manifest = context.artifact_root / "instruments-trace-manifest.json"
        write_json(
            manifest,
            {
                "schema_version": "turnvector.benchmark.instruments-trace.v1",
                "captures": captures,
            },
        )
        return (_local_artifact("instruments_trace", manifest, context.artifact_root),)


PERSISTENCE_REPLACEMENT_FAULTS = {
    "revision_mismatch": "snapshot_revision",
    "abi_mismatch": "snapshot_abi",
    "concurrent_reader_writer": "snapshot_publication",
}
PERSISTENCE_MUTATION_FAULTS = {
    "corruption": "snapshot_payload",
    "audit_tail_gap": "audit_journal",
    "duplicate_operation": "control_operation_log",
}
PERSISTENCE_PROCESS_FAULTS = {
    "interrupted_payload": "snapshot_payload_staged",
    "interrupted_metadata": "snapshot_metadata_staged",
    "restart": "runtime_ready",
    "pre_commit_crash": "control_pre_commit",
    "post_commit_pre_sync_crash": "control_post_commit_pre_sync",
    "indeterminate_effect": "external_effect_started",
}
MAX_CONCURRENT_PUBLICATION_IDENTITIES = 8


def _persistence_file(
    runtime_root: Path,
    value: Any,
    where: str,
    *,
    expected_role: str,
) -> Tuple[Path, Mapping[str, Any]]:
    descriptor = _strict_evidence_object(
        value, ["path", "size", "sha256", "role"], where
    )
    if descriptor["role"] != expected_role:
        raise ContractError(f"{where}.role must be {expected_role!r}")
    relative_text = descriptor["path"]
    if not isinstance(relative_text, str) or not relative_text:
        raise ContractError(f"{where}.path must be a non-empty relative path")
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ContractError(f"{where}.path escapes the Benchmark runtime root")
    root = runtime_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ContractError(f"{where}.path escapes the Benchmark runtime root") from error
    try:
        mode = os.lstat(path).st_mode
    except OSError as error:
        raise ContractError(f"{where}.path is unavailable: {error}") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ContractError(f"{where}.path must name a regular file directly")
    size = descriptor["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ContractError(f"{where}.size must be a non-negative integer")
    if path.stat().st_size != size:
        raise ContractError(f"{where}.size differs from the staged file")
    digest = descriptor["sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ContractError(f"{where}.sha256 must be a lowercase SHA256")
    if sha256_file(path) != digest:
        raise ContractError(f"{where}.sha256 differs from the staged file")
    return path, dict(descriptor)


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _process_has_stopped(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0 or result.stdout.strip().startswith("Z")


def _process_executable(process_id: int) -> Optional[Path]:
    proc_path = Path(f"/proc/{process_id}/exe")
    if proc_path.exists():
        try:
            return proc_path.resolve(strict=True)
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidpath = library.proc_pidpath
            proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            proc_pidpath.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(4096)
            length = proc_pidpath(process_id, buffer, len(buffer))
            if length > 0:
                return Path(buffer.value.decode("utf-8")).resolve(strict=True)
        except (AttributeError, OSError, UnicodeDecodeError):
            return None
    result = subprocess.run(
        ["ps", "-ww", "-o", "comm=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    candidate = Path(result.stdout.strip())
    if not candidate.is_absolute():
        return None
    try:
        return candidate.resolve(strict=True)
    except OSError:
        return None


def apply_persistence_fault(
    runtime_root: Path,
    fault: Any,
    stage_value: Any,
    allowed_binaries: Mapping[Path, str],
    *,
    termination_timeout_seconds: float = 5.0,
) -> Mapping[str, Any]:
    """Apply one real file/process fault and return Benchmark-owned custody evidence."""

    if not isinstance(fault, str):
        raise ContractError("persistence fault must be a string")
    known_faults = (
        {"none"}
        | set(PERSISTENCE_REPLACEMENT_FAULTS)
        | set(PERSISTENCE_MUTATION_FAULTS)
        | set(PERSISTENCE_PROCESS_FAULTS)
    )
    if fault not in known_faults:
        raise ContractError(f"unknown persistence fault: {fault!r}")
    stage = _strict_evidence_object(
        stage_value,
        [
            "process_id",
            "process_executable",
            "process_sha256",
            "fault_target",
            "replacement",
            "phase_marker",
        ],
        "persistence stage",
    )
    process_id = stage["process_id"]
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise ContractError("persistence stage.process_id must be a positive integer")
    if _process_has_stopped(process_id):
        raise ContractError("persistence target process exited before fault injection")
    executable_text = stage["process_executable"]
    if not isinstance(executable_text, str) or not Path(executable_text).is_absolute():
        raise ContractError("persistence stage.process_executable must be an absolute path")
    try:
        executable = Path(executable_text).resolve(strict=True)
    except OSError as error:
        raise ContractError(
            f"persistence stage.process_executable is unavailable: {error}"
        ) from error
    process_sha256 = stage["process_sha256"]
    allowed_sha256 = allowed_binaries.get(executable)
    if allowed_sha256 is None or process_sha256 != allowed_sha256:
        raise ContractError(
            "persistence process executable is not hash-bound by hello_ack.binary_manifest"
        )
    if sha256_file(executable) != process_sha256:
        raise ContractError("persistence process executable changed after hello")
    observed_executable = _process_executable(process_id)
    if observed_executable != executable:
        raise ContractError(
            "persistence stage process PID does not execute the declared binary"
        )

    evidence: Dict[str, Any] = {
        "fault": fault,
        "process_id": process_id,
        "process_executable": str(executable),
        "process_sha256": process_sha256,
        "action": "none",
        "process_terminated": False,
        "target_before": None,
        "target_after": None,
        "replacement_before": None,
        "phase_marker": None,
    }
    target_value = stage["fault_target"]
    replacement_value = stage["replacement"]
    marker_value = stage["phase_marker"]

    if fault == "none":
        if any(item is not None for item in (target_value, replacement_value, marker_value)):
            raise ContractError("persistence none case must not stage a fault target")
        return evidence

    if fault in PERSISTENCE_PROCESS_FAULTS:
        if target_value is not None or replacement_value is not None or marker_value is None:
            raise ContractError(
                f"persistence {fault} requires only its phase marker before process termination"
            )
        _, marker = _persistence_file(
            runtime_root,
            marker_value,
            "persistence stage.phase_marker",
            expected_role=PERSISTENCE_PROCESS_FAULTS[fault],
        )
        evidence["phase_marker"] = marker
        if _process_has_stopped(process_id):
            raise ContractError("persistence target process exited before SIGTERM")
        if _process_executable(process_id) != executable:
            raise ContractError(
                "persistence target process identity changed before SIGTERM"
            )
        try:
            os.kill(process_id, signal.SIGTERM)
        except ProcessLookupError as error:
            raise ContractError(
                "persistence target process exited before SIGTERM"
            ) from error
        deadline = time.monotonic() + termination_timeout_seconds
        while time.monotonic() < deadline and not _process_has_stopped(process_id):
            time.sleep(0.02)
        if not _process_has_stopped(process_id):
            raise ContractError(
                f"persistence target process {process_id} did not stop after SIGTERM"
            )
        evidence["action"] = "sigterm"
        evidence["process_terminated"] = True
        return evidence

    role = (
        PERSISTENCE_REPLACEMENT_FAULTS.get(fault)
        or PERSISTENCE_MUTATION_FAULTS[fault]
    )
    if target_value is None or marker_value is not None:
        raise ContractError(f"persistence {fault} requires one real file target")
    target, target_descriptor = _persistence_file(
        runtime_root,
        target_value,
        "persistence stage.fault_target",
        expected_role=role,
    )
    evidence["target_before"] = target_descriptor

    if fault in PERSISTENCE_REPLACEMENT_FAULTS:
        if replacement_value is None:
            raise ContractError(f"persistence {fault} requires a replacement file")
        replacement, replacement_descriptor = _persistence_file(
            runtime_root,
            replacement_value,
            "persistence stage.replacement",
            expected_role=role,
        )
        if replacement == target or replacement_descriptor["sha256"] == target_descriptor["sha256"]:
            raise ContractError("persistence replacement must have a different file identity")
        evidence["replacement_before"] = replacement_descriptor
        read_hashes: set[str] = set()
        stop_reader = threading.Event()

        def read_during_publication() -> None:
            while not stop_reader.is_set():
                try:
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    if len(read_hashes) < MAX_CONCURRENT_PUBLICATION_IDENTITIES:
                        read_hashes.add(digest)
                except FileNotFoundError:
                    pass
                stop_reader.wait(0.002)

        reader: Optional[threading.Thread] = None
        if fault == "concurrent_reader_writer":
            reader = threading.Thread(target=read_during_publication, daemon=True)
            reader.start()
            deadline = time.monotonic() + 1.0
            while not read_hashes and time.monotonic() < deadline:
                time.sleep(0.001)
        try:
            os.replace(replacement, target)
            _fsync_parent(target)
            if reader is not None:
                deadline = time.monotonic() + 1.0
                while len(read_hashes) < 2 and time.monotonic() < deadline:
                    time.sleep(0.001)
        finally:
            if reader is not None:
                stop_reader.set()
                reader.join(timeout=1.0)
        if reader is not None:
            if reader.is_alive():
                raise ContractError("Benchmark concurrent reader did not stop")
            if not read_hashes:
                raise ContractError("Benchmark concurrent reader captured no file identity")
            evidence["concurrent_read_sha256"] = sorted(read_hashes)
        evidence["action"] = "atomic-replace"
    elif replacement_value is not None:
        if fault != "duplicate_operation":
            raise ContractError(f"persistence {fault} must not stage a replacement file")
        replacement, replacement_descriptor = _persistence_file(
            runtime_root,
            replacement_value,
            "persistence stage.replacement",
            expected_role=role,
        )
        if not replacement.read_bytes():
            raise ContractError("persistence duplicate operation payload must not be empty")
        with target.open("ab") as stream:
            payload = replacement.read_bytes()
            stream.write(payload)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        evidence["replacement_before"] = replacement_descriptor
        evidence["action"] = "append-duplicate-operation"
    elif fault == "corruption":
        data = bytearray(target.read_bytes())
        if not data:
            raise ContractError("persistence corruption target must not be empty")
        data[len(data) // 2] ^= 0xA5
        with target.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        evidence["action"] = "flip-byte"
    elif fault == "audit_tail_gap":
        size = target.stat().st_size
        if size < 2:
            raise ContractError("persistence audit journal must contain at least two bytes")
        with target.open("r+b") as stream:
            stream.truncate(max(1, size // 2))
            stream.flush()
            os.fsync(stream.fileno())
        evidence["action"] = "truncate-tail"
    else:
        raise ContractError(f"persistence {fault} did not select a fault action")

    evidence["target_after"] = {
        "path": target.relative_to(runtime_root.resolve()).as_posix(),
        "size": target.stat().st_size,
        "sha256": sha256_file(target),
        "role": role,
    }
    if evidence["target_after"]["sha256"] == target_descriptor["sha256"]:
        raise ContractError("persistence file fault did not change the target identity")
    return evidence


class PersistenceAndRecoveryLaneRunner(EvidenceLaneRunner):
    lane_id = "persistence-and-recovery"

    def run(
        self, context: LaneContext, subject: SubjectSession, hello: SubjectHello
    ) -> LaneResult:
        try:
            return super().run(context, subject, hello)
        except DataPlaneEnvironmentError as error:
            return _lane_result_with_message(
                context, "environment_unavailable", str(error)
            )

    def execute_case_steps(
        self,
        context: LaneContext,
        subject: SubjectSession,
        hello: SubjectHello,
        case: PlannedCase,
        benchmark_runtime_root: Optional[Path],
    ) -> Tuple[
        List[Mapping[str, Any]],
        List[Mapping[str, Any]],
        Optional[Mapping[str, Any]],
    ]:
        if hello.identity.kind == "fixture":
            return super().execute_case_steps(
                context, subject, hello, case, benchmark_runtime_root
            )
        if benchmark_runtime_root is None:
            raise ContractError("persistence qualification requires a Benchmark runtime root")
        if tuple(case.operations) != (
            "stage-runtime-root",
            "observe-benchmark-fault",
            "restart-and-inspect",
        ):
            raise ContractError("persistence suite operations are not canonical")
        DataPlaneDescriptor.parse(hello.data_plane)
        allowed_binaries: Dict[Path, str] = {}
        for item in hello.binary_manifest:
            path = Path(item["path"])
            if not path.is_absolute():
                path = subject.adapter.cwd / path
            allowed_binaries[path.resolve()] = item["sha256"]
        common_payload = {
            "lane_id": case.lane_id,
            "matrix_id": case.matrix_id,
            "parameters": dict(case.parameters),
            "behavior_case_ids": list(case.behavior_case_ids),
            "execution_boundary": "benchmark_orchestrated",
            "external_inputs": dict(context.external_inputs),
            "benchmark_runtime_root": str(benchmark_runtime_root),
        }
        staged = subject.case_step(
            case.case_id, 1, case.operations[0], common_payload
        )
        staged_outer = _strict_evidence_object(
            staged, ["stage"], f"{case.case_id} persistence stage"
        )
        injected = apply_persistence_fault(
            benchmark_runtime_root,
            case.parameters.get("fault"),
            staged_outer["stage"],
            allowed_binaries,
        )
        observed = subject.case_step(
            case.case_id,
            2,
            case.operations[1],
            {**common_payload, "benchmark_fault": injected},
        )
        if observed != {"fault_observed": True}:
            raise ContractError(
                f"{case.case_id} fault observation must be exactly {{'fault_observed': true}}"
            )
        recovered = subject.case_step(
            case.case_id,
            3,
            case.operations[2],
            {**common_payload, "benchmark_fault": injected},
        )
        if set(recovered) != {"record"} or not isinstance(recovered["record"], dict):
            raise ContractError(
                f"{case.case_id} restart evidence must contain exactly one raw record"
            )
        records = [
            {
                "case_id": case.case_id,
                "phase": "step",
                "step_index": 1,
                "operation": case.operations[0],
                "evidence": dict(staged),
            },
            {
                "case_id": case.case_id,
                "phase": "benchmark_fault",
                "evidence": dict(injected),
            },
            {
                "case_id": case.case_id,
                "phase": "step",
                "step_index": 2,
                "operation": case.operations[1],
                "evidence": dict(observed),
            },
            {
                "case_id": case.case_id,
                "phase": "step",
                "step_index": 3,
                "operation": case.operations[2],
                "evidence": dict(recovered),
            },
        ]
        return [dict(staged), dict(observed), dict(recovered)], records, injected

    def benchmark_artifacts(
        self,
        context: LaneContext,
        case_collections: Sequence[Mapping[str, Any]],
    ) -> Tuple[Mapping[str, Any], ...]:
        if not case_collections:
            return ()
        path = context.artifact_root / "benchmark-fault-trace.json"
        write_json(
            path,
            {
                "schema_version": "turnvector.benchmark.persistence-fault-trace.v1",
                "cases": [dict(item) for item in case_collections],
            },
        )
        return (_local_artifact("fault_trace", path, context.artifact_root),)


class ProtocolAndWorkerSupervisionLaneRunner(RealDaemonEvidenceLaneRunner):
    lane_id = "protocol-and-worker-supervision"


class CertificationEnvelopesLaneRunner(RealDaemonEvidenceLaneRunner):
    lane_id = "certification-envelopes"


LANE_RUNNER_REGISTRY: Mapping[str, LaneRunner] = {
    runner.lane_id: runner
    for runner in (
        CoreEventReplayLaneRunner(),
        SchedulerPolicyLaneRunner(),
        SchedulerPerformanceLaneRunner(),
        RequestServingLifecycleLaneRunner(),
        MlxNativeCorrectnessLaneRunner(),
        BoundedTurnAndFfiLaneRunner(),
        ResidencyAndMemoryGovernorLaneRunner(),
        CrossModelServingLaneRunner(),
        ObservabilityQualificationLaneRunner(),
        PersistenceAndRecoveryLaneRunner(),
        ProtocolAndWorkerSupervisionLaneRunner(),
        CertificationEnvelopesLaneRunner(),
    )
}
