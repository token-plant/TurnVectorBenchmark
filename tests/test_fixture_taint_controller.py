from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import turnvector_benchmark.owner_lifecycle_fixture as owner_lifecycle_fixture
from turnvector_benchmark.controller import (
    LaneController,
    resolve_claimable,
    resolve_full_implementation_status,
)
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.expectation import load_expectation
from turnvector_benchmark.fixture_provenance import (
    BENCHMARK_FIXTURE,
    PRODUCTION_SUBJECT,
    CaseStartMonitor,
    ExecutionProvenance,
)
from turnvector_benchmark.lane_contract import (
    CasePlan,
    expand_case_plan,
    load_all_lane_suites,
)
from turnvector_benchmark.lane_runner import (
    LANE_RUNNER_REGISTRY,
    LaneContext,
    LaneResult,
)
from turnvector_benchmark.owner_lifecycle_fixture import (
    OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR,
    OWNER_LIFECYCLE_FIXTURE_ID,
)
from turnvector_benchmark.subject import SubjectHello, SubjectIdentity


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v2.json"
SUBJECT = ROOT / "subjects" / "reference-fixture-v1.json"
CERTIFICATION = ROOT / "certification" / "reference-fixture-v1.json"


class _PassingRunnerStub:
    """Dispatch stub that records the bound provenance and passes every case."""

    lane_id = "core-event-replay"

    def __init__(self) -> None:
        self.bound_provenance: Optional[tuple] = None

    def run(
        self, context: LaneContext, subject: Any, hello: Any
    ) -> LaneResult:
        del subject, hello
        self.bound_provenance = (
            context.execution_provenance,
            context.fixture_id,
        )
        return LaneResult(
            lane_id=context.lane.lane_id,
            status="passed",
            case_count=len(context.plan.cases),
            executed_case_count=len(context.plan.cases),
            metrics={},
            gates=(),
            failures=(),
            artifacts=(),
            raw_records=(),
        )


class _FakeImplementationSession:
    """SubjectAdapter v1 session double that presents an implementation subject."""

    def __init__(self, adapter: Any) -> None:
        del adapter
        self.transcript: list = []
        driver = ROOT / "drivers" / "reference_subject.py"
        self._binary = (
            {
                "path": str(driver),
                "sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            },
        )

    def __enter__(self) -> "_FakeImplementationSession":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def hello(self, run_id: str, lane_id: str, lane_protocol: str) -> SubjectHello:
        del run_id
        return SubjectHello(
            identity=SubjectIdentity(
                name="implementation-test-subject",
                version="1",
                kind="implementation",
                build_identity="reference-fixture-build-v1",
            ),
            supported_lanes={lane_id: lane_protocol},
            binary_manifest=self._binary,
            dependency_manifest=(),
            environment_identity={
                "device_class": "fixture",
                "memory_bytes": 1048576,
                "os_build": "fixture",
            },
            data_plane=None,
        )

    def finish(self) -> None:
        pass

    def stderr_text(self) -> str:
        return ""


class FixtureTaintControllerTests(unittest.TestCase):
    def output_path(self, name: str = "artifact") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / name

    def controller(self, output: Path) -> LaneController:
        return LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=SUBJECT,
            certification_record_path=CERTIFICATION,
            external_fixture_manifest_path=None,
            output_dir=output,
            target_repo=None,
        )

    def implementation_subject(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        value = json.loads(SUBJECT.read_text(encoding="utf-8"))
        value["subject_kind"] = "implementation"
        for adapter in value["adapters"]:
            adapter["cwd"] = str(ROOT)
        path = Path(temporary.name) / "implementation-subject.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _controller_with(self, output: Path) -> LaneController:
        return LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=self.implementation_subject(),
            certification_record_path=CERTIFICATION,
            external_fixture_manifest_path=None,
            output_dir=output,
            target_repo=None,
        )

    def test_run_fixture_taint_starts_clean(self) -> None:
        controller = self.controller(self.output_path())
        self.assertEqual(controller.run_fixture_taint, "clean")
        self.assertEqual(controller.fixture_ids, ())
        self.assertEqual(controller._case_start_monitor.started_lanes, ())

    def test_reference_fixture_run_binds_clean_taint_and_empty_fixture_ids(self) -> None:
        output = self.output_path()
        result = self.controller(output).run_lane("core-event-replay")
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.report["run_fixture_taint"], "clean")
        self.assertEqual(result.report["fixture_ids"], [])
        # The reference fixture subject remains nonclaimable without any taint.
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], "clean")
        self.assertEqual(manifest["fixture_ids"], [])

    def test_pre_start_transition_taints_before_first_case_execution(self) -> None:
        output = self.output_path()
        controller = self.controller(output)
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            result = controller.run_lane("protocol-and-owner-lifecycle")
        self.assertEqual(result.status, "passed")
        # All 24 cases executed after the absorbing transition was bound.
        self.assertEqual(result.report["lanes"][0]["executed_case_count"], 24)
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertTrue(
            controller._case_start_monitor.has_started("protocol-and-owner-lifecycle")
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])

    def test_taint_is_absorbing_across_lanes(self) -> None:
        output = self.output_path()
        controller = self.controller(output)
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            result = controller._run(
                ("protocol-and-owner-lifecycle", "core-event-replay")
            )
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])

        # Unit-level absorption: benchmark_fixture taints, production never
        # returns to clean, and only a fresh run resets the state machine.
        unit = self.controller(self.output_path("unit"))
        unit._apply_fixture_provenance(
            "a", ExecutionProvenance(BENCHMARK_FIXTURE, OWNER_LIFECYCLE_FIXTURE_ID)
        )
        self.assertEqual(unit.run_fixture_taint, "fixture_tainted")
        unit._apply_fixture_provenance(
            "b", ExecutionProvenance(PRODUCTION_SUBJECT, None)
        )
        self.assertEqual(unit.run_fixture_taint, "fixture_tainted")
        unit._apply_fixture_provenance(
            "a", ExecutionProvenance(BENCHMARK_FIXTURE, OWNER_LIFECYCLE_FIXTURE_ID)
        )
        self.assertEqual(unit.fixture_ids, (OWNER_LIFECYCLE_FIXTURE_ID,))
        unit._begin_fixture_taint_state()
        self.assertEqual(unit.run_fixture_taint, "clean")
        self.assertEqual(unit.fixture_ids, ())

    def test_final_run_artifacts_bind_taint_and_sorted_fixture_ids(self) -> None:
        output = self.output_path()
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            result = self.controller(output).run_lane("protocol-and-owner-lifecycle")
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        # The run manifest and the global report bind the exact same
        # absorbing run_fixture_taint and sorted fixture_ids state.
        self.assertEqual(manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        # SHA256SUMS covers the manifest and report that bind the taint state.
        checksums = (output / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        by_path = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in checksums}
        for name in ("manifest.json", "report.json"):
            self.assertIn(name, by_path)
            self.assertEqual(
                by_path[name],
                hashlib.sha256((output / name).read_bytes()).hexdigest(),
                name,
            )

    def test_claimability_evaluates_taint_before_every_other_axis(self) -> None:
        base = dict(
            fixture_subject=False,
            all_required_selected=True,
            aggregate_status="passed",
            evidence_valid=True,
            source_matches=True,
        )
        # fixture_tainted wins over every individual claimability axis.
        for field, altered in (
            ("fixture_subject", True),
            ("all_required_selected", False),
            ("aggregate_status", "gate_failed"),
            ("evidence_valid", False),
            ("source_matches", False),
        ):
            kwargs = dict(base)
            kwargs[field] = altered
            with self.subTest(field=field):
                self.assertEqual(
                    resolve_full_implementation_status(
                        run_fixture_taint="fixture_tainted",
                        fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID],
                        **kwargs,
                    ),
                    "not_claimable_fixture",
                )
                self.assertFalse(
                    resolve_claimable(
                        run_fixture_taint="fixture_tainted",
                        fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID],
                        **kwargs,
                    )
                )
        # The same all-passing inputs are claimable only while clean.
        self.assertEqual(
            resolve_full_implementation_status(run_fixture_taint="clean", **base),
            "passed",
        )
        self.assertTrue(resolve_claimable(run_fixture_taint="clean", **base))
        self.assertEqual(
            resolve_full_implementation_status(
                run_fixture_taint="fixture_tainted",
                fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID],
                **base,
            ),
            "not_claimable_fixture",
        )
        self.assertFalse(
            resolve_claimable(
                run_fixture_taint="fixture_tainted",
                fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID],
                **base,
            )
        )
        # The reference fixture subject alone stays nonclaimable while clean.
        fixture_kwargs = dict(base)
        fixture_kwargs["fixture_subject"] = True
        self.assertEqual(
            resolve_full_implementation_status(run_fixture_taint="clean", **fixture_kwargs),
            "not_claimable_fixture",
        )

    def test_implementation_subject_without_fixture_stays_clean(self) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        stub = _PassingRunnerStub()
        with patch.dict(
            "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
            {"core-event-replay": stub},
        ):
            with patch(
                "turnvector_benchmark.controller.SubjectSession",
                _FakeImplementationSession,
            ):
                result = controller.run_lane("core-event-replay")
        self.assertEqual(result.report["lanes"][0]["status"], "passed")
        self.assertEqual(result.report["run_fixture_taint"], "clean")
        self.assertEqual(result.report["fixture_ids"], [])
        self.assertEqual(result.report["full_implementation_status"], "not_evaluated")
        self.assertEqual(
            stub.bound_provenance, ("production_subject", None)
        )

    def test_fixture_taint_precedes_an_implementation_subject_at_controller_level(
        self,
    ) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        stub = _PassingRunnerStub()
        with patch.dict(
            "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
            {"core-event-replay": stub},
        ):
            with patch(
                "turnvector_benchmark.controller.SubjectSession",
                _FakeImplementationSession,
            ):
                with patch.dict(
                    "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
                    {"core-event-replay": OWNER_LIFECYCLE_FIXTURE_ID},
                ):
                    result = controller.run_lane("core-event-replay")
        self.assertEqual(result.report["lanes"][0]["status"], "passed")
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        # Even a non-fixture implementation subject with passing results is
        # never claimable while the run is fixture_tainted.
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertFalse(result.report["claimable"])
        self.assertEqual(
            stub.bound_provenance,
            (BENCHMARK_FIXTURE, OWNER_LIFECYCLE_FIXTURE_ID),
        )

    def test_missing_unknown_and_mismatched_provenance_fails_closed(self) -> None:
        # Missing fixture_id for benchmark_fixture.
        with self.assertRaises(ContractError):
            ExecutionProvenance(BENCHMARK_FIXTURE, None)
        # Unknown provenance value.
        with self.assertRaises(ContractError):
            ExecutionProvenance("driver_or_context", None)
        # Mismatched: fixture_id on production_subject.
        with self.assertRaises(ContractError):
            ExecutionProvenance(PRODUCTION_SUBJECT, OWNER_LIFECYCLE_FIXTURE_ID)

        # Controller-level: unknown fixture ID in the selection seam.
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"core-event-replay": "unknown-fixture"},
        ):
            with self.assertRaisesRegex(ContractError, "unknown benchmark fixture ID"):
                self.controller(self.output_path("unknown")).run_lane("core-event-replay")

        # Controller-level: driver/context disagreement (descriptor mismatch).
        mismatched = dict(OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR)
        mismatched["execution_provenance"] = PRODUCTION_SUBJECT
        with patch.dict(
            "turnvector_benchmark.owner_lifecycle_fixture.FIXTURE_DESCRIPTORS",
            {OWNER_LIFECYCLE_FIXTURE_ID: mismatched},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
                {"core-event-replay": OWNER_LIFECYCLE_FIXTURE_ID},
            ):
                with self.assertRaisesRegex(ContractError, "disagrees"):
                    self.controller(self.output_path("mismatch")).run_lane(
                        "core-event-replay"
                    )

    def test_descriptor_fixture_id_disagreement_fails_closed(self) -> None:
        # Descriptor fixture_id disagreement (not just execution_provenance
        # mismatch) fails closed at the controller level.
        mismatched = dict(OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR)
        mismatched["fixture_id"] = "another-fixture-id"
        with patch.dict(
            "turnvector_benchmark.owner_lifecycle_fixture.FIXTURE_DESCRIPTORS",
            {OWNER_LIFECYCLE_FIXTURE_ID: mismatched},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
                {"core-event-replay": OWNER_LIFECYCLE_FIXTURE_ID},
            ):
                with self.assertRaisesRegex(ContractError, "disagrees"):
                    self.controller(self.output_path("fixture-id-mismatch")).run_lane(
                        "core-event-replay"
                    )

    def test_lane_context_strict_provenance_rejects_inconsistent_pairs(self) -> None:
        expectation = load_expectation(EXPECTATION)
        lane = expectation.lane("core-event-replay")
        suite = load_all_lane_suites(expectation)[lane.lane_id]
        full_plan = expand_case_plan(lane, suite)
        plan = CasePlan(
            lane_id=lane.lane_id,
            suite_id=suite.suite_id,
            cases=(full_plan.cases[0],),
        )
        base = dict(
            run_id="strict-context",
            lane=lane,
            suite=suite,
            plan=plan,
            artifact_root=Path("."),
            frozen_thresholds={},
            external_inputs={},
            case_start_monitor=CaseStartMonitor(),
        )
        with self.assertRaises(ContractError):
            LaneContext(
                **base,
                execution_provenance="production_subject",
                fixture_id=OWNER_LIFECYCLE_FIXTURE_ID,
            )
        with self.assertRaises(ContractError):
            LaneContext(
                **base,
                execution_provenance="benchmark_fixture",
                fixture_id=None,
            )
        with self.assertRaises(ContractError):
            LaneContext(**base, execution_provenance="mystery", fixture_id=None)
        valid = LaneContext(
            **base,
            execution_provenance="benchmark_fixture",
            fixture_id=OWNER_LIFECYCLE_FIXTURE_ID,
        )
        self.assertEqual(valid.execution_provenance, "benchmark_fixture")

    def test_runners_mark_first_case_start_on_the_shared_monitor(self) -> None:
        controller = self.controller(self.output_path())
        result = controller.run_lane("protocol-and-owner-lifecycle")
        self.assertEqual(result.status, "passed")
        self.assertEqual(
            controller._case_start_monitor.started_lanes,
            ("protocol-and-owner-lifecycle",),
        )

    def test_pr4_activation_renames_the_registry_and_activates_the_seam(self) -> None:
        expectation = load_expectation(EXPECTATION)
        expected_lanes = {lane.lane_id for lane in expectation.lanes}
        self.assertEqual(set(LANE_RUNNER_REGISTRY), expected_lanes)
        self.assertEqual(len(LANE_RUNNER_REGISTRY), 12)
        self.assertIn("protocol-and-owner-lifecycle", LANE_RUNNER_REGISTRY)
        self.assertNotIn("protocol-and-worker-supervision", LANE_RUNNER_REGISTRY)
        self.assertEqual(
            owner_lifecycle_fixture.FIXTURE_SELECTION_SEAM,
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        )
        summary = LaneController.inspect(expectation_path=EXPECTATION, target_repo=None)
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["registered_lane_runner_count"], 12)
        self.assertEqual(summary["qualification_case_count"], 425)
        self.assertEqual(summary["self_test_gate_count"], 58)


if __name__ == "__main__":
    unittest.main()
