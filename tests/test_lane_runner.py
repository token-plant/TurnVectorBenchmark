from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.expectation import load_expectation
from turnvector_benchmark.lane_contract import CasePlan, expand_case_plan, load_all_lane_suites
from turnvector_benchmark.lane_runner import EvidenceLaneRunner, LaneContext


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v1.json"


class _FailingSubject:
    def case_open(self, *args: Any, **kwargs: Any) -> str:
        return "ready"


class _FailingCollectorRunner(EvidenceLaneRunner):
    lane_id = "core-event-replay"

    def begin_case_collection(self, *args: Any, **kwargs: Any) -> object:
        return object()

    def execute_case_steps(self, *args: Any, **kwargs: Any) -> Any:
        raise ContractError("case execution failed")

    def end_case_collection(self, collector: Any) -> Any:
        raise RuntimeError("collector cleanup failed")


class LaneRunnerTests(unittest.TestCase):
    def test_collector_cleanup_failure_preserves_case_failure(self) -> None:
        expectation = load_expectation(EXPECTATION)
        lane = expectation.lane("core-event-replay")
        suite = load_all_lane_suites(expectation)[lane.lane_id]
        full_plan = expand_case_plan(lane, suite)
        plan = CasePlan(
            lane_id=full_plan.lane_id,
            suite_id=full_plan.suite_id,
            cases=(full_plan.cases[0],),
        )
        with tempfile.TemporaryDirectory() as directory:
            context = LaneContext(
                run_id="test-run",
                lane=lane,
                suite=suite,
                plan=plan,
                artifact_root=Path(directory),
                frozen_thresholds={gate.metric: gate.expected for gate in lane.gates},
                external_inputs={},
            )

            with self.assertRaisesRegex(
                ContractError,
                "case execution failed.*collector cleanup failed",
            ):
                _FailingCollectorRunner().run(context, _FailingSubject(), None)  # type: ignore[arg-type]
            cleanup_evidence = next(
                Path(directory).glob("cases/*/collector-cleanup-error.json")
            )
            self.assertTrue(cleanup_evidence.is_file())


if __name__ == "__main__":
    unittest.main()
