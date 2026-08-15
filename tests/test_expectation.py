from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError, load_suite
from turnvector_benchmark.expectation import (
    bind_suite_lane,
    expectation_summary,
    load_expectation,
)


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "expectations" / "turnvector-implementation-v1.json"
SUITE = ROOT / "suites" / "scheduler-policy-v1.json"


class ImplementationExpectationTests(unittest.TestCase):
    def test_checked_in_expectation_is_complete_and_not_claimable(self) -> None:
        expectation = load_expectation(MANIFEST)
        summary = expectation_summary(expectation, None)

        self.assertEqual(summary["required_lane_count"], 12)
        self.assertEqual(summary["executable_required_lane_count"], 1)
        self.assertEqual(summary["contract_only_required_lane_count"], 11)
        self.assertFalse(summary["full_run_available"])
        self.assertEqual(summary["claim_status"], "not_evaluated")
        self.assertIn(
            "mlx-native-correctness", summary["unexecutable_required_lane_ids"]
        )
        self.assertIn(
            "scheduler-performance", summary["unexecutable_required_lane_ids"]
        )

    def test_mlx_expectation_fixes_native_matrix_and_parity_gates(self) -> None:
        expectation = load_expectation(MANIFEST)
        lane = expectation.lane("mlx-native-correctness")
        matrices = {
            matrix.matrix_id: {
                dimension.dimension_id: dimension.values
                for dimension in matrix.dimensions
            }
            for matrix in lane.matrices
        }
        gates = {gate.gate_id: gate for gate in lane.gates}

        self.assertEqual(matrices["decode"]["model_architecture"], ("dense", "moe"))
        self.assertEqual(matrices["decode"]["batch_size"], (1, 4))
        self.assertEqual(matrices["prefill"]["prompt_tokens"], (64, 256, 1024))
        self.assertEqual(gates["output-parity"].expected, 0)
        self.assertEqual(gates["logits-parity"].expected, 0)
        self.assertEqual(gates["kv-parity"].expected, 0)
        self.assertEqual(lane.harness.status, "contract_only")
        self.assertTrue(lane.required)

    def test_scheduler_suite_is_bound_to_exact_executable_lane(self) -> None:
        expectation = load_expectation(MANIFEST)
        suite = load_suite(SUITE)
        lane = bind_suite_lane(expectation, "scheduler-policy", suite)
        self.assertEqual(lane.harness.status, "executable")

        with self.assertRaisesRegex(ContractError, "not an executable JSONL suite"):
            bind_suite_lane(expectation, "mlx-native-correctness", suite)

    def test_unknown_expectation_field_fails_closed(self) -> None:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        value["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expectation.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unknown fields"):
                load_expectation(path)


if __name__ == "__main__":
    unittest.main()
