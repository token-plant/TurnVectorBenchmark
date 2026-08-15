from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import (
    Candidate,
    ContractError,
    ModelConfig,
    SchedulerOracle,
    load_scenario,
    load_suite,
)


ROOT = Path(__file__).resolve().parent.parent


def candidate(
    candidate_id: str,
    model_id: str,
    *,
    timing_obligation_us=None,
    resource_safe=True,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        model_id=model_id,
        execution_phase="decode",
        service_class="standard",
        engine_service_bound_us=100,
        runtime_overhead_bound_us=20,
        timing_obligation_us=timing_obligation_us,
        capability_authorized=True,
        resource_safe=resource_safe,
        timing_feasible=True,
        output_reserved=True,
    )


class ScenarioContractTests(unittest.TestCase):
    def test_checked_in_suite_is_valid(self) -> None:
        suite = load_suite(ROOT / "suites" / "scheduler-policy-v1.json")
        self.assertEqual(suite.suite_id, "scheduler-policy-v1")
        self.assertEqual([item.total_turns for item in suite.scenarios], [48, 20, 4])

    def test_unknown_scenario_field_fails_closed(self) -> None:
        source = ROOT / "scenarios" / "weighted-service-1-to-3.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unknown fields"):
                load_scenario(path)


class SchedulerOracleTests(unittest.TestCase):
    def test_urgent_selection_happens_after_safety_filtering(self) -> None:
        oracle = SchedulerOracle(
            [
                ModelConfig("alpha", 1),
                ModelConfig("beta", 1),
                ModelConfig("gamma", 1),
            ]
        )
        initial, _, _ = oracle.schedule(
            0, [candidate("beta.decode", "beta"), candidate("alpha.decode", "alpha")]
        )
        self.assertEqual(initial.candidate_id, "alpha.decode")
        oracle.accept_receipt("alpha.decode", "alpha", 100)

        selected, decision_class, ledgers = oracle.schedule(
            120,
            [
                candidate("gamma.prefill", "gamma", timing_obligation_us=120, resource_safe=False),
                candidate("beta.decode", "beta"),
                candidate("alpha.decode", "alpha", timing_obligation_us=240),
            ],
        )
        self.assertEqual(selected.candidate_id, "alpha.decode")
        self.assertEqual(decision_class, "urgent")
        self.assertNotIn("gamma", ledgers)

    def test_reentering_model_aligns_without_idle_credit(self) -> None:
        oracle = SchedulerOracle([ModelConfig("alpha", 1), ModelConfig("beta", 1)])
        both = [candidate("alpha.decode", "alpha"), candidate("beta.decode", "beta")]
        for _ in range(4):
            selected, _, _ = oracle.schedule(0, both)
            oracle.accept_receipt(selected.candidate_id, selected.model_id, 100)
        for _ in range(8):
            selected, _, _ = oracle.schedule(0, [candidate("alpha.decode", "alpha")])
            oracle.accept_receipt(selected.candidate_id, selected.model_id, 100)

        selected, _, ledgers = oracle.schedule(0, list(reversed(both)))
        self.assertEqual(ledgers["alpha"], ledgers["beta"])
        self.assertEqual(selected.model_id, "alpha")


if __name__ == "__main__":
    unittest.main()
