from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import turnvector_benchmark.owner_lifecycle_fixture as owner_lifecycle_fixture
from turnvector_benchmark.controller import LaneController
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.fixture_provenance import (
    BENCHMARK_FIXTURE,
    PRODUCTION_SUBJECT,
    ExecutionProvenance,
)
from turnvector_benchmark.lane_runner import LaneContext, LaneResult
from turnvector_benchmark.owner_lifecycle_fixture import OWNER_LIFECYCLE_FIXTURE_ID
from turnvector_benchmark.subject import SubjectHello, SubjectIdentity


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v2.json"
SUBJECT = ROOT / "subjects" / "reference-fixture-v1.json"
CERTIFICATION = ROOT / "certification" / "reference-fixture-v1.json"


def _passed_result(context: LaneContext) -> LaneResult:
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


class _PassingRunnerStub:
    """Dispatch stub that records the bound provenance and passes every case."""

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
        return _passed_result(context)


class _StartMarkingRunnerStub:
    """Runner double that records provenance, marks its first CasePlan START,
    and optionally mutates the fixture selection seam for another lane."""

    def __init__(
        self,
        *,
        mark_start: bool = True,
        select_lane: Optional[str] = None,
    ) -> None:
        self.mark_start = mark_start
        self.select_lane = select_lane
        self.bound_provenance: Optional[tuple] = None

    def run(
        self, context: LaneContext, subject: Any, hello: Any
    ) -> LaneResult:
        del subject, hello
        self.bound_provenance = (
            context.execution_provenance,
            context.fixture_id,
        )
        if self.mark_start:
            context.case_start_monitor.mark_case_started(context.lane.lane_id)
        if self.select_lane is not None:
            owner_lifecycle_fixture.FIXTURE_SELECTION_SEAM[self.select_lane] = (
                OWNER_LIFECYCLE_FIXTURE_ID
            )
        return _passed_result(context)


class _RaiseAfterSeamMutationRunnerStub:
    """Runner double that marks its first CasePlan START, mutates the selection
    seam to the known owner fixture, and then raises RuntimeError."""

    def __init__(self, *, select_lane: str) -> None:
        self.select_lane = select_lane
        self.bound_provenance: Optional[tuple] = None

    def run(
        self, context: LaneContext, subject: Any, hello: Any
    ) -> LaneResult:
        del subject, hello
        self.bound_provenance = (
            context.execution_provenance,
            context.fixture_id,
        )
        context.case_start_monitor.mark_case_started(context.lane.lane_id)
        owner_lifecycle_fixture.FIXTURE_SELECTION_SEAM[self.select_lane] = (
            OWNER_LIFECYCLE_FIXTURE_ID
        )
        raise RuntimeError("runner double failed after START and seam mutation")


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


class FixtureTaintLateSelectionTests(unittest.TestCase):
    def output_path(self, name: str = "artifact") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / name

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

    def test_late_fixture_selection_fails_closed_and_still_taints(self) -> None:
        controller = self._controller_with(self.output_path())
        # The late-selection boundary is global: a start in one lane closes
        # new selections for every lane, not just the started lane.
        controller._case_start_monitor.mark_case_started("core-event-replay")
        frozen = ExecutionProvenance(PRODUCTION_SUBJECT, None)
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            with self.assertRaisesRegex(ContractError, "late benchmark fixture selection"):
                controller._bind_and_revalidate_provenance(
                    "protocol-and-owner-lifecycle", frozen
                )
        # The attempted late selection still leaves the run fixture_tainted.
        self.assertEqual(controller.run_fixture_taint, "fixture_tainted")
        self.assertEqual(controller.fixture_ids, (OWNER_LIFECYCLE_FIXTURE_ID,))

    def test_unchanged_preselected_provenance_is_not_late(self) -> None:
        controller = self._controller_with(self.output_path())
        controller._case_start_monitor.mark_case_started("core-event-replay")
        frozen = ExecutionProvenance(BENCHMARK_FIXTURE, OWNER_LIFECYCLE_FIXTURE_ID)
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            # Unchanged frozen provenance is usable in a later lane even after
            # another lane started; it is not misclassified as late.
            controller._bind_and_revalidate_provenance(
                "protocol-and-owner-lifecycle", frozen
            )
        self.assertEqual(controller.run_fixture_taint, "fixture_tainted")
        self.assertEqual(controller.fixture_ids, (OWNER_LIFECYCLE_FIXTURE_ID,))

    def test_global_cross_lane_late_selection_fails_closed_and_taints(self) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        first = _StartMarkingRunnerStub(mark_start=True, select_lane="scheduler-policy")
        second = _PassingRunnerStub()
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
                {"core-event-replay": first, "scheduler-policy": second},
            ):
                with patch(
                    "turnvector_benchmark.controller.SubjectSession",
                    _FakeImplementationSession,
                ):
                    result = controller._run(("core-event-replay", "scheduler-policy"))
        # Lane 1 issued its first CasePlan START and then the seam attempted a
        # NEW fixture selection for another lane: the mutating lane fails
        # closed for the late selection right after its runner returns, and
        # the target lane's own bind revalidation fails the same way; the run
        # remains fixture_tainted.
        self.assertEqual(result.report["lanes"][0]["status"], "contract_failed")
        self.assertEqual(result.report["lanes"][1]["status"], "contract_failed")
        self.assertIn(
            "late benchmark fixture selection",
            result.report["lanes"][0]["failures"][0]["message"],
        )
        self.assertIn(
            "late benchmark fixture selection",
            result.report["lanes"][1]["failures"][0]["message"],
        )
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], result.report["run_fixture_taint"])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])

    def test_selection_mutation_between_freeze_and_bind_fails_closed_and_taints(
        self,
    ) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        first = _StartMarkingRunnerStub(mark_start=False, select_lane="scheduler-policy")
        second = _PassingRunnerStub()
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
                {"core-event-replay": first, "scheduler-policy": second},
            ):
                with patch(
                    "turnvector_benchmark.controller.SubjectSession",
                    _FakeImplementationSession,
                ):
                    result = controller._run(("core-event-replay", "scheduler-policy"))
        # The seam changed after the pre-run snapshot but before any case
        # START: the changed selection names a known fixture, so it taints
        # first and then fails closed for the disagreement. The mutating
        # runner's lane fails right after it returns, and the target lane's
        # own bind revalidation fails the same way.
        self.assertEqual(result.report["lanes"][0]["status"], "contract_failed")
        self.assertEqual(result.report["lanes"][1]["status"], "contract_failed")
        self.assertIn(
            "changed after the pre-run snapshot",
            result.report["lanes"][0]["failures"][0]["message"],
        )
        self.assertIn(
            "changed after the pre-run snapshot",
            result.report["lanes"][1]["failures"][0]["message"],
        )
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], result.report["run_fixture_taint"])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])

    def test_runner_mutating_own_seam_after_start_fails_closed_and_taints(self) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        stub = _StartMarkingRunnerStub(mark_start=True, select_lane="core-event-replay")
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
                {"core-event-replay": stub},
            ):
                with patch(
                    "turnvector_benchmark.controller.SubjectSession",
                    _FakeImplementationSession,
                ):
                    result = controller.run_lane("core-event-replay")
        # The only runner issued its first CasePlan START and then mutated its
        # own lane's fixture selection: the post-run revalidation fails the
        # lane closed for the global late selection while the run remains
        # fixture_tainted with the known sorted fixture ID.
        self.assertEqual(result.status, "contract_failed")
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.report["lanes"][0]["status"], "contract_failed")
        self.assertIn(
            "late benchmark fixture selection",
            result.report["lanes"][0]["failures"][0]["message"],
        )
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertFalse(result.report["claimable"])
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])
        self.assertEqual(report["fixture_ids"], result.report["fixture_ids"])

    def test_runner_raising_after_start_seam_mutation_taints_and_fails_contract(
        self,
    ) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        stub = _RaiseAfterSeamMutationRunnerStub(select_lane="core-event-replay")
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
                {"core-event-replay": stub},
            ):
                with patch(
                    "turnvector_benchmark.controller.SubjectSession",
                    _FakeImplementationSession,
                ):
                    result = controller.run_lane("core-event-replay")
        # The runner marked its first CasePlan START, mutated its own lane's
        # seam to the known owner fixture, and then raised RuntimeError: the
        # finally-guarded post-run revalidation applies the absorbing taint
        # first and fails the lane closed for the global late selection, so the
        # run normalizes to contract_failed even though the runner also threw.
        self.assertEqual(result.status, "contract_failed")
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.report["lanes"][0]["status"], "contract_failed")
        self.assertIn(
            "late benchmark fixture selection",
            result.report["lanes"][0]["failures"][0]["message"],
        )
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertFalse(result.report["claimable"])
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])
        self.assertEqual(report["fixture_ids"], result.report["fixture_ids"])

    def test_last_lane_mutating_an_already_run_lane_fails_closed_and_taints(
        self,
    ) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        first = _PassingRunnerStub()
        second = _StartMarkingRunnerStub(mark_start=True, select_lane="core-event-replay")
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {},
        ):
            with patch.dict(
                "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
                {"core-event-replay": first, "scheduler-policy": second},
            ):
                with patch(
                    "turnvector_benchmark.controller.SubjectSession",
                    _FakeImplementationSession,
                ):
                    result = controller._run(("core-event-replay", "scheduler-policy"))
        # The last lane issued its first CasePlan START and then mutated the
        # seam for the already-run first lane. The already-run lane's own
        # post-run revalidation had already passed, so it stays passed; the
        # mutating last lane fails closed for the global late selection and
        # the run remains fixture_tainted.
        self.assertEqual(result.report["lanes"][0]["status"], "passed")
        self.assertEqual(result.report["lanes"][1]["status"], "contract_failed")
        self.assertIn(
            "late benchmark fixture selection",
            result.report["lanes"][1]["failures"][0]["message"],
        )
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], result.report["run_fixture_taint"])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])

    def test_unchanged_preselected_fixture_works_in_later_lane_after_another_started(
        self,
    ) -> None:
        output = self.output_path()
        controller = self._controller_with(output)
        first = _StartMarkingRunnerStub(mark_start=True)
        second = _PassingRunnerStub()
        with patch.dict(
            "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
            {"core-event-replay": first, "scheduler-policy": second},
        ):
            with patch(
                "turnvector_benchmark.controller.SubjectSession",
                _FakeImplementationSession,
            ):
                with patch.dict(
                    "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
                    {"scheduler-policy": OWNER_LIFECYCLE_FIXTURE_ID},
                ):
                    result = controller._run(("core-event-replay", "scheduler-policy"))
        # The preselected fixture was frozen before any START, so binding it in
        # the later lane is allowed even though lane 1 already started.
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.report["lanes"][0]["status"], "passed")
        self.assertEqual(result.report["lanes"][1]["status"], "passed")
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertEqual(
            first.bound_provenance, (PRODUCTION_SUBJECT, None)
        )
        self.assertEqual(
            second.bound_provenance, (BENCHMARK_FIXTURE, OWNER_LIFECYCLE_FIXTURE_ID)
        )


if __name__ == "__main__":
    unittest.main()
