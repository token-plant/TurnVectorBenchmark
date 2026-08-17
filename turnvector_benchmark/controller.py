from __future__ import annotations

import json
import platform
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import __version__
from .core import ContractError
from .evidence import (
    git_evidence_still_valid,
    git_identity,
    sha256_file,
    validate_binary_manifest,
    write_checksums,
    write_json,
    write_jsonl,
)
from .external import ExternalFixtureManifest, load_external_fixture_manifest
from .expectation import (
    ImplementationExpectation,
    expectation_summary,
    inspect_source_contract,
    load_expectation,
)
from .lane_contract import (
    SUBJECT_PROTOCOL,
    CasePlan,
    CertificationRecord,
    LaneSuite,
    SubjectAdapter,
    SubjectManifest,
    expand_case_plan,
    load_all_lane_suites,
    load_certification_record,
    load_subject_manifest,
    resolve_gate_threshold,
    validate_certification_contract,
    validate_certification_identity,
)
from .lane_oracles import ANALYZERS
from .lane_runner import LANE_RUNNER_REGISTRY, LaneContext, LaneResult
from .subject import SubjectHello, SubjectSession


RUN_SCHEMA = "turnvector.benchmark.run.v2"
REPORT_SCHEMA = "turnvector.benchmark.report.v2"
SELF_TEST_COVERAGE_SCHEMA = "turnvector.benchmark.self-test-coverage.v1"

STATUS_EXIT_CODES = {
    "passed": 0,
    "contract_failed": 2,
    "gate_failed": 3,
    "unsupported": 4,
    "environment_unavailable": 5,
    "infrastructure_failed": 6,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _lane_failure_result(
    lane_id: str,
    plan: CasePlan,
    status: str,
    message: str,
    *,
    kind: Optional[str] = None,
    raw_records: Sequence[Mapping[str, Any]] = (),
) -> LaneResult:
    return LaneResult(
        lane_id=lane_id,
        status=status,
        case_count=len(plan.cases),
        executed_case_count=0,
        metrics={},
        gates=(),
        failures=(
            {
                "kind": kind or status,
                "message": message,
            },
        ),
        artifacts=(),
        raw_records=tuple(raw_records),
    )


def _status_for_results(results: Sequence[LaneResult]) -> str:
    statuses = {result.status for result in results}
    for status in (
        "infrastructure_failed",
        "contract_failed",
        "gate_failed",
        "environment_unavailable",
        "unsupported",
    ):
        if status in statuses:
            return status
    return "passed"


@dataclass(frozen=True)
class ControllerResult:
    status: str
    exit_code: int
    artifact_dir: Path
    report: Mapping[str, Any]


class LaneController:
    """Deep control surface from immutable expectation to fail-closed evidence."""

    def __init__(
        self,
        *,
        expectation_path: Path,
        subject_manifest_path: Optional[Path],
        certification_record_path: Optional[Path],
        external_fixture_manifest_path: Optional[Path],
        output_dir: Path,
        target_repo: Optional[Path],
        profile: str = "qualification",
    ) -> None:
        if profile != "qualification":
            raise ContractError("only the qualification profile is defined in benchmark v1")
        self.expectation_path = expectation_path.resolve()
        self.subject_manifest_path = (
            subject_manifest_path.resolve() if subject_manifest_path else None
        )
        self.certification_record_path = (
            certification_record_path.resolve() if certification_record_path else None
        )
        self.external_fixture_manifest_path = (
            external_fixture_manifest_path.resolve()
            if external_fixture_manifest_path
            else None
        )
        self.output_dir = output_dir.resolve()
        self.target_repo = target_repo.resolve() if target_repo else None
        self.profile = profile
        self.benchmark_repo = Path(__file__).resolve().parent.parent
        self.expectation: ImplementationExpectation = load_expectation(self.expectation_path)
        self.suites: Mapping[str, LaneSuite] = load_all_lane_suites(self.expectation)
        self.subject_manifest: Optional[SubjectManifest] = (
            None
            if self.subject_manifest_path is None
            else load_subject_manifest(self.subject_manifest_path, self.expectation)
        )
        self.certification_record: Optional[CertificationRecord] = (
            None
            if self.certification_record_path is None
            else load_certification_record(self.certification_record_path)
        )
        self.certification_contract_error: Optional[str] = None
        if self.certification_record is not None:
            try:
                validate_certification_contract(
                    self.certification_record, self.expectation
                )
            except ContractError as error:
                self.certification_contract_error = str(error)
        reference_lock = self.benchmark_repo / "oracles" / "mlx" / "reference-lock-v1.json"
        self.external_fixtures: Optional[ExternalFixtureManifest] = (
            None
            if self.external_fixture_manifest_path is None
            else load_external_fixture_manifest(
                self.external_fixture_manifest_path, reference_lock=reference_lock
            )
        )
        self._validate_registry()

    def _validate_registry(self) -> None:
        expected = {lane.lane_id for lane in self.expectation.lanes}
        observed = set(LANE_RUNNER_REGISTRY)
        if observed != expected:
            raise ContractError(
                "fixed lane runner registry differs from expectation: "
                f"missing={sorted(expected - observed)!r}, unknown={sorted(observed - expected)!r}"
            )
        for lane in self.expectation.lanes:
            runner = LANE_RUNNER_REGISTRY[lane.lane_id]
            suite = self.suites[lane.lane_id]
            if runner.lane_id != lane.harness.runner or suite.runner != runner.lane_id:
                raise ContractError(f"runner binding for lane {lane.lane_id!r} is inconsistent")
        expected_oracles = expected - {"scheduler-policy"}
        if set(ANALYZERS) != expected_oracles:
            raise ContractError(
                "fixed normalized-evidence oracle registry differs from expectation lanes"
            )

    @classmethod
    def inspect(
        cls,
        *,
        expectation_path: Path,
        target_repo: Optional[Path],
    ) -> Mapping[str, Any]:
        expectation = load_expectation(expectation_path.resolve())
        suites = load_all_lane_suites(expectation)
        expected_lanes = {lane.lane_id for lane in expectation.lanes}
        runner_lanes = set(LANE_RUNNER_REGISTRY)
        oracle_lanes = set(ANALYZERS)
        coverage_path = Path(__file__).resolve().parent.parent / "selftests" / "coverage-v1.json"
        coverage = cls._load_self_test_coverage(coverage_path, expectation)
        plans = {
            lane.lane_id: expand_case_plan(lane, suites[lane.lane_id])
            for lane in expectation.lanes
        }
        summary = dict(expectation_summary(expectation, target_repo))
        summary.update(
            {
                "status": "ready",
                "lane_suite_count": len(suites),
                "registered_lane_runner_count": len(runner_lanes),
                "runner_registry_complete": runner_lanes == expected_lanes,
                "evidence_oracle_count": len(oracle_lanes) + 1,
                "oracle_registry_complete": oracle_lanes
                == expected_lanes - {"scheduler-policy"},
                "self_test_gate_count": len(coverage["gate_ids"]),
                "self_test_coverage_complete": True,
                "qualification_case_count": sum(len(plan.cases) for plan in plans.values()),
                "lane_case_counts": {
                    lane_id: len(plan.cases) for lane_id, plan in plans.items()
                },
                "readiness": "derived_complete",
                "claim_status": "not_evaluated",
            }
        )
        return summary

    @staticmethod
    def _load_self_test_coverage(
        path: Path, expectation: ImplementationExpectation
    ) -> Mapping[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContractError(f"cannot read self-test coverage {path}: {error}") from error
        if not isinstance(value, dict) or set(value) != {"schema_version", "gate_ids"}:
            raise ContractError("self-test coverage must contain schema_version and gate_ids")
        if value["schema_version"] != SELF_TEST_COVERAGE_SCHEMA:
            raise ContractError("self-test coverage schema version is invalid")
        if not isinstance(value["gate_ids"], list) or any(
            not isinstance(item, str) for item in value["gate_ids"]
        ):
            raise ContractError("self-test coverage gate_ids must be a string array")
        expected = [
            f"{lane.lane_id}.{gate.gate_id}"
            for lane in expectation.lanes
            for gate in lane.gates
        ]
        if value["gate_ids"] != expected:
            raise ContractError("self-test coverage must list every gate in expectation order")
        return value

    def run_lane(self, lane_id: str) -> ControllerResult:
        self.expectation.lane(lane_id)
        return self._run((lane_id,))

    def run_all(self) -> ControllerResult:
        return self._run(tuple(lane.lane_id for lane in self.expectation.lanes if lane.required))

    def _run(self, lane_ids: Sequence[str]) -> ControllerResult:
        if self.output_dir.exists():
            raise FileExistsError(f"output directory already exists: {self.output_dir}")
        self.output_dir.mkdir(parents=True)
        started_at_value = datetime.now(timezone.utc)
        started_at = started_at_value.isoformat().replace("+00:00", "Z")
        run_id = uuid.uuid4().hex
        benchmark_before = git_identity(self.benchmark_repo)
        target_before = git_identity(self.target_repo) if self.target_repo else None
        source_contract = inspect_source_contract(self.expectation, self.target_repo)
        plans = {
            lane_id: expand_case_plan(
                self.expectation.lane(lane_id), self.suites[lane_id]
            )
            for lane_id in lane_ids
        }
        pre_run_thresholds = self._pre_run_threshold_manifest(
            lane_ids, observed_at=started_at_value
        )
        manifest = {
            "schema_version": RUN_SCHEMA,
            "run_id": run_id,
            "started_at": started_at,
            "profile": self.profile,
            "expectation": {
                "id": self.expectation.expectation_id,
                "schema_version": self.expectation.schema_version,
                "path": str(self.expectation.source_path),
                "sha256": sha256_file(self.expectation.source_path),
            },
            "subject_manifest": self._file_identity(self.subject_manifest_path),
            "certification_record": self._file_identity(self.certification_record_path),
            "external_fixture_manifest": self._file_identity(
                self.external_fixture_manifest_path
            ),
            "pre_run_thresholds": pre_run_thresholds,
            "certification_contract_failure": self.certification_contract_error,
            "requested_lane_ids": list(lane_ids),
            "qualification_case_count": sum(len(plan.cases) for plan in plans.values()),
            "benchmark_git_before": benchmark_before,
            "target_git_before": target_before,
            "source_contract": source_contract,
        }
        write_json(self.output_dir / "manifest.json", manifest)
        results: List[LaneResult] = []
        subject_identities: Dict[str, Mapping[str, Any]] = {}
        for lane_id in lane_ids:
            lane_dir = self.output_dir / "lanes" / lane_id
            lane_dir.mkdir(parents=True)
            plan = plans[lane_id]
            write_json(lane_dir / "case-plan.json", plan.as_dict())
            result, subject_identity = self._run_one_lane(
                run_id=run_id,
                lane_id=lane_id,
                lane_dir=lane_dir,
                plan=plan,
                frozen_thresholds=pre_run_thresholds["values"][lane_id],
                threshold_failures=pre_run_thresholds["failures"].get(lane_id, ()),
            )
            results.append(result)
            if subject_identity is not None:
                subject_identities[lane_id] = subject_identity
            write_json(
                lane_dir / "manifest.json",
                {
                    "schema_version": "turnvector.benchmark.lane-manifest.v1",
                    "run_id": run_id,
                    "lane_id": lane_id,
                    "suite": {
                        "id": self.suites[lane_id].suite_id,
                        "path": str(self.suites[lane_id].source_path),
                        "sha256": sha256_file(self.suites[lane_id].source_path),
                    },
                    "case_schema": {
                        "path": str(self.suites[lane_id].case_schema_path),
                        "sha256": sha256_file(self.suites[lane_id].case_schema_path),
                    },
                    "case_count": len(plan.cases),
                    "thresholds": pre_run_thresholds["values"][lane_id],
                    "claim_scope": list(self.expectation.lane(lane_id).claim_scope),
                },
            )
            write_json(
                lane_dir / "environment.json",
                {
                    "benchmark_git_before": benchmark_before,
                    "target_git_before": target_before,
                    "subject": subject_identity,
                    "external_inputs": (
                        None
                        if self.external_fixtures is None
                        else {
                            artifact_id: artifact.as_dict()
                            for artifact_id, artifact in self.external_fixtures.artifacts.items()
                            if artifact_id
                            in self.suites[lane_id].requirements.external_inputs
                        }
                    ),
                },
            )
            self._write_lane_result(lane_dir, result)
            write_checksums(lane_dir)
        benchmark_after = git_identity(self.benchmark_repo)
        target_after = git_identity(self.target_repo) if self.target_repo else None
        evidence_valid = git_evidence_still_valid(benchmark_before, benchmark_after)
        if target_before is not None and target_after is not None:
            evidence_valid = evidence_valid and git_evidence_still_valid(
                target_before, target_after
            )
        aggregate_status = _status_for_results(results)
        if not evidence_valid:
            aggregate_status = "infrastructure_failed"
        all_required_selected = set(lane_ids) == {
            lane.lane_id for lane in self.expectation.lanes if lane.required
        }
        fixture_subject = (
            self.subject_manifest is not None
            and self.subject_manifest.subject_kind == "fixture"
        )
        implementation_passed = (
            all_required_selected
            and aggregate_status == "passed"
            and evidence_valid
            and not fixture_subject
            and source_contract.get("matches") is True
        )
        if fixture_subject:
            full_status = "not_claimable_fixture"
        elif not all_required_selected:
            full_status = "not_evaluated"
        elif implementation_passed:
            full_status = "passed"
        else:
            full_status = "failed"
        report = {
            "schema_version": REPORT_SCHEMA,
            "run_id": run_id,
            "status": aggregate_status,
            "full_implementation_status": full_status,
            "claimable": implementation_passed,
            "profile": self.profile,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "required_lane_count": sum(lane.required for lane in self.expectation.lanes),
            "selected_lane_count": len(lane_ids),
            "passed_lane_count": sum(result.status == "passed" for result in results),
            "observed_lane_statuses": sorted({result.status for result in results}),
            "lane_status_counts": {
                status: sum(result.status == status for result in results)
                for status in sorted({result.status for result in results})
            },
            "qualification_case_count": sum(result.case_count for result in results),
            "executed_case_count": sum(result.executed_case_count for result in results),
            "evidence_valid": evidence_valid,
            "source_contract": source_contract,
            "benchmark_git_before": benchmark_before,
            "benchmark_git_after": benchmark_after,
            "target_git_before": target_before,
            "target_git_after": target_after,
            "subject_identities": subject_identities,
            "lanes": [result.as_dict() for result in results],
        }
        if not evidence_valid:
            report["evidence_invalidation"] = (
                "benchmark or target Git HEAD/status changed during execution"
            )
        write_json(self.output_dir / "report.json", report)
        write_json(
            self.output_dir / "environment.json",
            {
                "python": sys.version,
                "platform": platform.platform(),
                "machine": platform.machine(),
                "benchmark_version": __version__,
                "subject_protocol": SUBJECT_PROTOCOL,
            },
        )
        write_checksums(self.output_dir)
        return ControllerResult(
            status=aggregate_status,
            exit_code=STATUS_EXIT_CODES[aggregate_status],
            artifact_dir=self.output_dir,
            report=report,
        )

    def _pre_run_threshold_manifest(
        self,
        lane_ids: Sequence[str],
        *,
        observed_at: Optional[datetime] = None,
    ) -> Mapping[str, Any]:
        resolved_at = observed_at or datetime.now(timezone.utc)
        thresholds: Dict[str, Dict[str, Any]] = {}
        failures: Dict[str, List[Mapping[str, str]]] = {}
        for lane_id in lane_ids:
            lane_values: Dict[str, Any] = {}
            lane_failures: List[Mapping[str, str]] = []
            for gate in self.expectation.lane(lane_id).gates:
                try:
                    lane_values[gate.metric] = resolve_gate_threshold(
                        lane_id,
                        gate,
                        self.certification_record,
                        observed_at=resolved_at,
                    )
                except ContractError as error:
                    lane_failures.append(
                        {
                            "gate_id": gate.gate_id,
                            "metric": gate.metric,
                            "message": str(error),
                        }
                    )
            thresholds[lane_id] = lane_values
            if lane_failures:
                failures[lane_id] = lane_failures
        complete = not failures
        return {
            "resolved_at": resolved_at.isoformat().replace("+00:00", "Z"),
            "values": thresholds,
            "failures": failures,
            "complete": complete,
            "frozen": complete,
        }

    @staticmethod
    def _file_identity(path: Optional[Path]) -> Optional[Mapping[str, Any]]:
        if path is None:
            return None
        return {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}

    def _run_one_lane(
        self,
        *,
        run_id: str,
        lane_id: str,
        lane_dir: Path,
        plan: CasePlan,
        frozen_thresholds: Mapping[str, Any],
        threshold_failures: Sequence[Mapping[str, str]],
    ) -> Tuple[LaneResult, Optional[Mapping[str, Any]]]:
        lane = self.expectation.lane(lane_id)
        suite = self.suites[lane_id]
        if self.certification_contract_error is not None:
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "contract_failed",
                    self.certification_contract_error,
                ),
                None,
            )
        if threshold_failures:
            details = "; ".join(
                f"{failure['gate_id']}: {failure['message']}"
                for failure in threshold_failures
            )
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "contract_failed",
                    f"pre-run threshold snapshot is incomplete: {details}",
                ),
                None,
            )
        adapter = (
            None
            if self.subject_manifest is None
            else self.subject_manifest.adapter_for_lane(lane_id)
        )
        if adapter is None:
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "unsupported",
                    "subject manifest has no adapter for this required lane",
                ),
                None,
            )
        if adapter.category != suite.adapter_category:
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "contract_failed",
                    f"adapter category {adapter.category!r} does not match suite "
                    f"category {suite.adapter_category!r}",
                ),
                None,
            )
        external_inputs: Dict[str, Mapping[str, Any]] = {}
        if (
            self.subject_manifest is not None
            and self.subject_manifest.subject_kind == "implementation"
            and suite.requirements.external_inputs
        ):
            if self.external_fixtures is None:
                return (
                    _lane_failure_result(
                        lane_id,
                        plan,
                        "environment_unavailable",
                        "implementation lane requires a hash-verified external fixture manifest",
                    ),
                    None,
                )
            missing = sorted(
                set(suite.requirements.external_inputs)
                - set(self.external_fixtures.artifacts)
            )
            if missing:
                return (
                    _lane_failure_result(
                        lane_id,
                        plan,
                        "environment_unavailable",
                        f"external fixture manifest is missing required inputs: {missing!r}",
                    ),
                    None,
                )
            external_inputs = {
                artifact_id: self.external_fixtures.artifacts[artifact_id].as_dict()
                for artifact_id in suite.requirements.external_inputs
            }
        raw_root = lane_dir / "raw"
        raw_root.mkdir()
        session: Optional[SubjectSession] = None
        identity_value: Optional[Mapping[str, Any]] = None
        try:
            with SubjectSession(adapter) as subject:
                session = subject
                hello = subject.hello(run_id, lane_id, suite.protocol)
                identity_value = self._validate_hello(adapter, hello)
                if lane_id not in hello.supported_lanes:
                    result = _lane_failure_result(
                        lane_id,
                        plan,
                        "unsupported",
                        "subject hello does not declare support for this required lane",
                    )
                elif hello.supported_lanes[lane_id] != suite.protocol:
                    result = _lane_failure_result(
                        lane_id,
                        plan,
                        "unsupported",
                        "subject lane protocol does not exactly match the suite protocol",
                    )
                else:
                    self._validate_execution_descriptor(lane_id, suite, hello)
                    if self.certification_record is not None:
                        validate_certification_identity(
                            self.certification_record,
                            subject_build_identity=hello.identity.build_identity,
                            environment_identity=hello.environment_identity,
                            plan=plan,
                        )
                    context = LaneContext(
                        run_id=run_id,
                        lane=lane,
                        suite=suite,
                        plan=plan,
                        artifact_root=raw_root,
                        frozen_thresholds=frozen_thresholds,
                        external_inputs=external_inputs,
                    )
                    result = LANE_RUNNER_REGISTRY[lane_id].run(context, subject, hello)
                subject.finish()
                write_jsonl(lane_dir / "subject-transcript.jsonl", subject.transcript)
                if subject.stderr_text():
                    (lane_dir / "subject.stderr.log").write_text(
                        subject.stderr_text() + "\n", encoding="utf-8"
                    )
                return result, identity_value
        except ContractError as error:
            transcript = () if session is None else tuple(session.transcript)
            if transcript:
                write_jsonl(lane_dir / "subject-transcript.jsonl", transcript)
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "contract_failed",
                    str(error),
                    raw_records=transcript,
                ),
                identity_value,
            )
        except (OSError, RuntimeError) as error:
            transcript = () if session is None else tuple(session.transcript)
            if transcript:
                write_jsonl(lane_dir / "subject-transcript.jsonl", transcript)
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "infrastructure_failed",
                    str(error),
                    raw_records=transcript,
                ),
                identity_value,
            )
        except Exception as error:  # Preserve unexpected judge failures as evidence.
            transcript = () if session is None else tuple(session.transcript)
            if transcript:
                write_jsonl(lane_dir / "subject-transcript.jsonl", transcript)
            write_json(
                lane_dir / "infrastructure-exception.json",
                {"error": repr(error), "traceback": traceback.format_exc()},
            )
            return (
                _lane_failure_result(
                    lane_id,
                    plan,
                    "infrastructure_failed",
                    repr(error),
                    raw_records=transcript,
                ),
                identity_value,
            )

    def _validate_hello(
        self,
        adapter: SubjectAdapter,
        hello: SubjectHello,
    ) -> Mapping[str, Any]:
        if self.subject_manifest is None:
            raise ContractError("subject manifest disappeared during execution")
        if hello.identity.kind != self.subject_manifest.subject_kind:
            raise ContractError(
                "subject hello kind does not match subject manifest; fixture identity cannot be "
                "presented as an implementation"
            )
        verified_binaries = validate_binary_manifest(hello.binary_manifest, adapter.cwd)
        return {
            "name": hello.identity.name,
            "version": hello.identity.version,
            "kind": hello.identity.kind,
            "build_identity": hello.identity.build_identity,
            "supported_lanes": dict(hello.supported_lanes),
            "binary_manifest": verified_binaries,
            "dependency_manifest": [dict(item) for item in hello.dependency_manifest],
            "environment_identity": dict(hello.environment_identity),
            "data_plane": hello.data_plane,
        }

    @staticmethod
    def _validate_execution_descriptor(
        lane_id: str, suite: LaneSuite, hello: SubjectHello
    ) -> None:
        if (
            suite.requirements.execution_boundary == "direct_data_plane"
            and hello.identity.kind == "implementation"
            and hello.data_plane is None
        ):
            raise ContractError(
                f"implementation lane {lane_id!r} requires a real Data Plane descriptor"
            )

    @staticmethod
    def _write_lane_result(lane_dir: Path, result: LaneResult) -> None:
        write_json(lane_dir / "metrics.json", dict(result.metrics))
        write_json(lane_dir / "gates.json", [dict(item) for item in result.gates])
        write_json(lane_dir / "failures.json", [dict(item) for item in result.failures])
        write_json(lane_dir / "report.json", result.as_dict())
        if result.raw_records:
            write_jsonl(lane_dir / "raw-evidence.jsonl", result.raw_records)
