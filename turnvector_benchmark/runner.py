from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import __version__
from .core import (
    DRIVER_PROTOCOL,
    ContractError,
    ConformanceError,
    Scenario,
    SchedulerOracle,
    Suite,
    canonical_json,
    fraction_map,
    fraction_text,
)
from .driver import DriverProcess
from .expectation import (
    ExpectationLane,
    ImplementationExpectation,
    inspect_source_contract,
)


ARTIFACT_SCHEMA = "turnvector.benchmark.artifact.v1"
REPORT_SCHEMA = "turnvector.benchmark.report.v1"


@dataclass(frozen=True)
class RunResult:
    status: str
    exit_code: int
    artifact_dir: Path
    report: Dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_identity(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", top, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", top, "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return {"path": str(resolved), "git": False}
    return {
        "path": top,
        "git": True,
        "head": head,
        "dirty": bool(status),
        "status_short": status,
    }


def _require_response(
    response: Mapping[str, Any], required: Sequence[str], where: str
) -> None:
    expected = set(required)
    actual = set(response)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise ContractError(f"{where} has invalid fields ({', '.join(details)})")


def _ledger_response(value: Any, expected_models: Sequence[str], where: str) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    if set(value) != set(expected_models):
        raise ContractError(
            f"{where} model keys differ: expected={sorted(expected_models)!r}, "
            f"observed={sorted(value)!r}"
        )
    result: Dict[str, str] = {}
    for model_id, ledger in value.items():
        if not isinstance(ledger, str):
            raise ContractError(f"{where}.{model_id} must be a canonical fraction string")
        try:
            parsed = Fraction(ledger)
        except (ValueError, ZeroDivisionError) as error:
            raise ContractError(f"{where}.{model_id} is not a fraction: {ledger!r}") from error
        if ledger != fraction_text(parsed):
            raise ContractError(f"{where}.{model_id} is not canonical: {ledger!r}")
        result[model_id] = ledger
    return result


def _plan_hash(records: Sequence[Mapping[str, Any]]) -> str:
    identity = [
        {
            "sequence": record["sequence"],
            "candidate_id": record["observed_candidate_id"],
            "decision_class": record["decision_class"],
            "runnable_ledgers_us": record["observed_runnable_ledgers_us"],
            "receipt_ledgers_us": record.get("observed_receipt_ledgers_us"),
        }
        for record in records
    ]
    return _sha256_bytes(canonical_json(identity).encode("utf-8"))


def _driver_file_hashes(command_argv: Sequence[str], cwd: Path) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    seen = set()
    for index, argument in enumerate(command_argv):
        candidate = Path(argument)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if index == 0 and not candidate.is_file():
            executable = shutil.which(argument)
            if executable:
                candidate = Path(executable)
        if candidate.is_file():
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append({"path": str(resolved), "sha256": _sha256_file(resolved)})
    return files


class BenchmarkRunner:
    def __init__(
        self,
        suite: Suite,
        expectation: ImplementationExpectation,
        lane: ExpectationLane,
        driver_command: str,
        driver_cwd: Path,
        output_dir: Path,
        target_repo: Optional[Path],
        response_timeout_seconds: float,
    ) -> None:
        self.suite = suite
        self.expectation = expectation
        self.lane = lane
        self.driver_command = driver_command
        self.driver_cwd = driver_cwd.resolve()
        self.output_dir = output_dir.resolve()
        self.target_repo = target_repo.resolve() if target_repo else None
        self.response_timeout_seconds = response_timeout_seconds
        self.trace: List[Dict[str, Any]] = []
        self.driver_identities: List[Dict[str, str]] = []

    def _handshake(self, driver: DriverProcess) -> None:
        response = driver.request(
            {"kind": "hello", "protocol_version": DRIVER_PROTOCOL}
        )
        _require_response(
            response,
            ["kind", "protocol_version", "driver_name", "driver_version"],
            "hello response",
        )
        if response["kind"] != "hello_ack" or response["protocol_version"] != DRIVER_PROTOCOL:
            raise ContractError(f"driver rejected protocol {DRIVER_PROTOCOL!r}: {response!r}")
        if not isinstance(response["driver_name"], str) or not response["driver_name"]:
            raise ContractError("hello response driver_name must be a non-empty string")
        if not isinstance(response["driver_version"], str) or not response["driver_version"]:
            raise ContractError("hello response driver_version must be a non-empty string")
        identity = {
            "driver_name": response["driver_name"],
            "driver_version": response["driver_version"],
        }
        if identity not in self.driver_identities:
            self.driver_identities.append(identity)

    def _initialize(
        self, driver: DriverProcess, scenario: Scenario, repetition: int
    ) -> None:
        response = driver.request(
            {
                "kind": "initialize",
                "scenario_id": scenario.scenario_id,
                "repetition": repetition,
                "models": [model.as_message() for model in scenario.models],
            }
        )
        _require_response(
            response,
            ["kind", "scenario_id", "model_ledgers_us"],
            "initialize response",
        )
        if response["kind"] != "initialized" or response["scenario_id"] != scenario.scenario_id:
            raise ContractError(f"invalid initialize response: {response!r}")
        expected = {model.model_id: "0/1" for model in scenario.models}
        observed = _ledger_response(
            response["model_ledgers_us"], list(expected), "initialize.model_ledgers_us"
        )
        if observed != expected:
            raise ConformanceError(
                f"{scenario.scenario_id} initialized ledgers differ: "
                f"expected={expected!r}, observed={observed!r}"
            )

    def _run_repetition(
        self, scenario: Scenario, repetition: int
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        oracle = SchedulerOracle(scenario.models)
        sequence = 0
        now_us = scenario.clock_start_us
        records: List[Dict[str, Any]] = []
        selection_count = {model.model_id: 0 for model in scenario.models}
        engine_service_us = {model.model_id: 0 for model in scenario.models}
        current_wait = {model.model_id: 0 for model in scenario.models}
        max_wait = {model.model_id: 0 for model in scenario.models}
        max_consecutive = {model.model_id: 0 for model in scenario.models}
        last_model: Optional[str] = None
        consecutive = 0
        urgent_selections = 0
        normal_selections = 0
        no_plan_count = 0

        with DriverProcess(
            self.driver_command, self.driver_cwd, self.response_timeout_seconds
        ) as driver:
            self._handshake(driver)
            self._initialize(driver, scenario, repetition)
            for segment_index, segment in enumerate(scenario.segments):
                for segment_turn in range(segment.turns):
                    sequence += 1
                    candidates = [template.at_time(now_us) for template in segment.candidates]
                    actual_by_id = {
                        template.candidate_id: template.actual_engine_service_us
                        for template in segment.candidates
                    }
                    expected, decision_class, expected_runnable = oracle.schedule(
                        now_us, candidates
                    )
                    expected_id = expected.candidate_id if expected else None
                    schedule_message = {
                        "kind": "schedule",
                        "sequence": sequence,
                        "now_us": now_us,
                        "resource_mode": segment.resource_mode,
                        "candidates": [candidate.as_message() for candidate in candidates],
                    }
                    response = driver.request(schedule_message)
                    _require_response(
                        response,
                        ["kind", "sequence", "candidate_id", "runnable_ledgers_us"],
                        "plan response",
                    )
                    if response["kind"] != "plan" or response["sequence"] != sequence:
                        raise ContractError(f"invalid plan response: {response!r}")
                    observed_id = response["candidate_id"]
                    if observed_id is not None and not isinstance(observed_id, str):
                        raise ContractError("plan.candidate_id must be a string or null")
                    expected_runnable_text = fraction_map(expected_runnable)
                    observed_runnable = _ledger_response(
                        response["runnable_ledgers_us"],
                        list(expected_runnable_text),
                        "plan.runnable_ledgers_us",
                    )
                    record: Dict[str, Any] = {
                        "schema_version": "turnvector.benchmark.trace_record.v1",
                        "scenario_id": scenario.scenario_id,
                        "repetition": repetition,
                        "segment": segment_index,
                        "segment_turn": segment_turn,
                        "sequence": sequence,
                        "now_us": now_us,
                        "resource_mode": segment.resource_mode,
                        "candidates": schedule_message["candidates"],
                        "decision_class": decision_class,
                        "expected_candidate_id": expected_id,
                        "observed_candidate_id": observed_id,
                        "expected_runnable_ledgers_us": expected_runnable_text,
                        "observed_runnable_ledgers_us": observed_runnable,
                    }
                    records.append(record)
                    self.trace.append(record)
                    if observed_runnable != expected_runnable_text:
                        record["status"] = "failed"
                        record["failure"] = "runnable ledger state mismatch"
                        raise ConformanceError(
                            f"{scenario.scenario_id} repetition {repetition} sequence {sequence}: "
                            f"runnable ledgers expected={expected_runnable_text!r}, "
                            f"observed={observed_runnable!r}"
                        )
                    if observed_id != expected_id:
                        record["status"] = "failed"
                        record["failure"] = "selected candidate mismatch"
                        raise ConformanceError(
                            f"{scenario.scenario_id} repetition {repetition} sequence {sequence}: "
                            f"expected candidate {expected_id!r}, observed {observed_id!r}"
                        )

                    eligible_models = {
                        candidate.model_id for candidate in candidates if candidate.eligible
                    }
                    for model_id in current_wait:
                        if model_id in eligible_models and model_id != (expected.model_id if expected else None):
                            current_wait[model_id] += 1
                            max_wait[model_id] = max(max_wait[model_id], current_wait[model_id])
                        elif model_id in eligible_models:
                            current_wait[model_id] = 0
                        else:
                            current_wait[model_id] = 0

                    if expected is None:
                        no_plan_count += 1
                        oracle.clear_no_plan()
                        record["status"] = "passed"
                        now_us += segment.clock_step_us
                        continue

                    if decision_class == "urgent":
                        urgent_selections += 1
                    else:
                        normal_selections += 1
                    selection_count[expected.model_id] += 1
                    actual_service = actual_by_id[expected.candidate_id]
                    engine_service_us[expected.model_id] += actual_service
                    if last_model == expected.model_id:
                        consecutive += 1
                    else:
                        last_model = expected.model_id
                        consecutive = 1
                    max_consecutive[expected.model_id] = max(
                        max_consecutive[expected.model_id], consecutive
                    )

                    expected_all_ledgers = fraction_map(
                        oracle.accept_receipt(
                            expected.candidate_id, expected.model_id, actual_service
                        )
                    )
                    receipt_response = driver.request(
                        {
                            "kind": "receipt",
                            "sequence": sequence,
                            "candidate_id": expected.candidate_id,
                            "model_id": expected.model_id,
                            "actual_engine_service_us": actual_service,
                        }
                    )
                    _require_response(
                        receipt_response,
                        ["kind", "sequence", "model_ledgers_us"],
                        "receipt response",
                    )
                    if (
                        receipt_response["kind"] != "receipt_accepted"
                        or receipt_response["sequence"] != sequence
                    ):
                        raise ContractError(f"invalid receipt response: {receipt_response!r}")
                    observed_all_ledgers = _ledger_response(
                        receipt_response["model_ledgers_us"],
                        list(expected_all_ledgers),
                        "receipt.model_ledgers_us",
                    )
                    record["actual_engine_service_us"] = actual_service
                    record["expected_receipt_ledgers_us"] = expected_all_ledgers
                    record["observed_receipt_ledgers_us"] = observed_all_ledgers
                    if observed_all_ledgers != expected_all_ledgers:
                        record["status"] = "failed"
                        record["failure"] = "receipt ledger state mismatch"
                        raise ConformanceError(
                            f"{scenario.scenario_id} repetition {repetition} sequence {sequence}: "
                            f"receipt ledgers expected={expected_all_ledgers!r}, "
                            f"observed={observed_all_ledgers!r}"
                        )
                    record["status"] = "passed"
                    now_us += segment.clock_step_us
            driver.finish()

        total_service = sum(engine_service_us.values())
        shares = {
            model_id: (
                fraction_text(Fraction(service, total_service)) if total_service else "0/1"
            )
            for model_id, service in sorted(engine_service_us.items())
        }
        normalized = {
            model.model_id: Fraction(engine_service_us[model.model_id], model.weight)
            for model in scenario.models
        }
        spread = max(normalized.values()) - min(normalized.values())
        metrics = {
            "turns": sequence,
            "selection_count": selection_count,
            "engine_service_us": engine_service_us,
            "engine_service_share": shares,
            "normalized_engine_service_us": fraction_map(normalized),
            "normalized_service_spread_us": fraction_text(spread),
            "urgent_selection_count": urgent_selections,
            "normal_selection_count": normal_selections,
            "no_plan_count": no_plan_count,
            "max_runnable_wait_turns": max_wait,
            "max_consecutive_turns": max_consecutive,
            "plan_trace_sha256": _plan_hash(records),
        }
        return metrics, records

    def _run_scenario(self, scenario: Scenario) -> Dict[str, Any]:
        repetitions: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        for repetition in range(scenario.repetitions):
            try:
                metrics, _ = self._run_repetition(scenario, repetition)
                repetitions.append({"repetition": repetition, "status": "passed", "metrics": metrics})
            except ConformanceError as error:
                errors.append(
                    {"repetition": str(repetition), "class": "conformance", "message": str(error)}
                )
                repetitions.append({"repetition": repetition, "status": "conformance_failed"})
            except ContractError as error:
                errors.append(
                    {"repetition": str(repetition), "class": "contract", "message": str(error)}
                )
                repetitions.append({"repetition": repetition, "status": "contract_failed"})

        hashes = [
            item["metrics"]["plan_trace_sha256"]
            for item in repetitions
            if item["status"] == "passed"
        ]
        determinism_passed = len(hashes) == scenario.repetitions and len(set(hashes)) == 1
        if not determinism_passed and not any(error["class"] == "contract" for error in errors):
            errors.append(
                {
                    "repetition": "all",
                    "class": "conformance",
                    "message": "canonical plan traces differ across repetitions",
                }
            )
        if any(error["class"] == "contract" for error in errors):
            status = "contract_failed"
        elif errors:
            status = "conformance_failed"
        else:
            status = "passed"
        return {
            "scenario_id": scenario.scenario_id,
            "description": scenario.description,
            "status": status,
            "gates": {
                "exact_selection": status == "passed",
                "exact_receipt_accounting": status == "passed",
                "deterministic_replay": determinism_passed,
            },
            "repetitions": repetitions,
            "errors": errors,
        }

    def _environment(self, started_at: str, completed_at: str) -> Dict[str, Any]:
        package_root = Path(__file__).resolve().parent.parent
        command_argv = shlex.split(self.driver_command)
        return {
            "schema_version": "turnvector.benchmark.environment.v1",
            "started_at": started_at,
            "completed_at": completed_at,
            "host": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": sys.version,
                "logical_cpu_count": os.cpu_count(),
            },
            "benchmark_repository": _git_identity(package_root),
            "target_repository": _git_identity(self.target_repo),
            "source_contract": inspect_source_contract(
                self.expectation, self.target_repo
            ),
            "driver": {
                "command_argv": command_argv,
                "cwd": str(self.driver_cwd),
                "file_hashes": _driver_file_hashes(command_argv, self.driver_cwd),
                "reported_identities": self.driver_identities,
                "response_timeout_seconds": self.response_timeout_seconds,
            },
        }

    def _manifest(self, run_id: str) -> Dict[str, Any]:
        package_root = Path(__file__).resolve().parent.parent
        inputs = [self.expectation.source_path, self.suite.source_path] + [
            scenario.source_path for scenario in self.suite.scenarios
        ]
        source_files = sorted((package_root / "turnvector_benchmark").glob("*.py"))
        source_files.extend(sorted((package_root / "schemas").glob("*.json")))
        unevaluated_required_lanes = [
            lane.lane_id
            for lane in self.expectation.lanes
            if lane.required and lane.lane_id != self.lane.lane_id
        ]
        return {
            "schema_version": ARTIFACT_SCHEMA,
            "run_id": run_id,
            "benchmark_version": __version__,
            "driver_protocol": DRIVER_PROTOCOL,
            "implementation_expectation": {
                "id": self.expectation.expectation_id,
                "path": str(self.expectation.source_path),
                "sha256": _sha256_file(self.expectation.source_path),
                "source_repository": self.expectation.source_contract.repository,
                "source_revision": self.expectation.source_contract.revision,
            },
            "lane": {
                "id": self.lane.lane_id,
                "layer": self.lane.layer,
                "required": self.lane.required,
                "harness_status": self.lane.harness.status,
            },
            "suite": {
                "id": self.suite.suite_id,
                "description": self.suite.description,
                "path": str(self.suite.source_path),
            },
            "inputs": [
                {"path": str(path), "sha256": _sha256_file(path)} for path in inputs
            ],
            "benchmark_source": [
                {
                    "path": str(path.relative_to(package_root)),
                    "sha256": _sha256_file(path),
                }
                for path in source_files
            ],
            "claim_scope": list(self.lane.claim_scope),
            "benchmark_scope_status": "partial_lane",
            "unevaluated_required_lanes": unevaluated_required_lanes,
        }

    def _summary(self, report: Mapping[str, Any]) -> str:
        lines = [
            "# TurnVector Benchmark Summary",
            "",
            f"Status: **{report['status']}**",
            "",
            "| Scenario | Status | Exact selection | Receipt accounting | Replay |",
            "|---|---|---:|---:|---:|",
        ]
        for scenario in report["scenarios"]:
            gates = scenario["gates"]
            lines.append(
                f"| `{scenario['scenario_id']}` | {scenario['status']} | "
                f"{'pass' if gates['exact_selection'] else 'fail'} | "
                f"{'pass' if gates['exact_receipt_accounting'] else 'fail'} | "
                f"{'pass' if gates['deterministic_replay'] else 'fail'} |"
            )
        lines.extend(
            [
                "",
                f"This artifact evaluates only the required `{self.lane.lane_id}` lane of",
                f"`{self.expectation.expectation_id}`. Every other required lane remains",
                "not evaluated; this result is not a complete implementation result.",
                "",
            ]
        )
        return "\n".join(lines)

    def run(self) -> RunResult:
        self.output_dir.mkdir(parents=True, exist_ok=False)
        started_at = _utc_now()
        run_id = str(uuid.uuid4())
        scenario_reports = [self._run_scenario(scenario) for scenario in self.suite.scenarios]
        completed_at = _utc_now()
        statuses = {scenario["status"] for scenario in scenario_reports}
        if "contract_failed" in statuses:
            status = "contract_failed"
            exit_code = 2
        elif "conformance_failed" in statuses:
            status = "conformance_failed"
            exit_code = 3
        else:
            status = "passed"
            exit_code = 0
        report: Dict[str, Any] = {
            "schema_version": REPORT_SCHEMA,
            "run_id": run_id,
            "status": status,
            "benchmark_scope_status": "partial_lane",
            "implementation_expectation_id": self.expectation.expectation_id,
            "lane_id": self.lane.lane_id,
            "required_lane_count": sum(
                lane.required for lane in self.expectation.lanes
            ),
            "unevaluated_required_lane_ids": [
                lane.lane_id
                for lane in self.expectation.lanes
                if lane.required and lane.lane_id != self.lane.lane_id
            ],
            "full_implementation_status": "not_evaluated",
            "suite_id": self.suite.suite_id,
            "scenario_count": len(scenario_reports),
            "passed_scenario_count": sum(
                scenario["status"] == "passed" for scenario in scenario_reports
            ),
            "scenarios": scenario_reports,
        }
        _write_json(self.output_dir / "manifest.json", self._manifest(run_id))
        _write_json(
            self.output_dir / "environment.json",
            self._environment(started_at, completed_at),
        )
        (self.output_dir / "trace.jsonl").write_text(
            "".join(canonical_json(record) + "\n" for record in self.trace),
            encoding="utf-8",
        )
        _write_json(self.output_dir / "report.json", report)
        (self.output_dir / "summary.md").write_text(
            self._summary(report), encoding="utf-8"
        )
        artifact_names = [
            "environment.json",
            "manifest.json",
            "report.json",
            "summary.md",
            "trace.jsonl",
        ]
        checksum_lines = [
            f"{_sha256_file(self.output_dir / name)}  {name}" for name in artifact_names
        ]
        (self.output_dir / "SHA256SUMS").write_text(
            "\n".join(checksum_lines) + "\n", encoding="ascii"
        )
        return RunResult(status, exit_code, self.output_dir, report)
