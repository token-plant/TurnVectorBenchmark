"""Slice E frozen regression gate tests."""

import math
import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.regression import (
    FrozenRegressionGates,
    RegressionGateSpec,
    evaluate_regression_gates,
    freeze_regression_gates,
)


class CrossEngineRegressionTests(unittest.TestCase):
    @staticmethod
    def gates():
        return freeze_regression_gates(
            [
                {
                    "id": "ttft-increase",
                    "metric": "ttft_increase_ratio",
                    "operator": "lte",
                    "expected": 0.10,
                    "decision": "promotion",
                },
                {
                    "id": "throughput-ratio",
                    "metric": "throughput_ratio",
                    "operator": "gte",
                    "expected": 0.95,
                    "decision": "promotion",
                },
                {
                    "id": "new-output-violations",
                    "metric": "new_output_violation_count",
                    "operator": "eq",
                    "expected": 0,
                    "decision": "promotion",
                },
            ]
        )

    def test_gate_spec_is_strict_immutable_and_hash_bound(self):
        frozen = self.gates()
        self.assertIsInstance(frozen, FrozenRegressionGates)
        self.assertEqual(len(frozen.sha256), 64)
        self.assertEqual(frozen.as_dict()["gates"][0]["id"], "ttft-increase")
        self.assertEqual(frozen.sha256, self.gates().sha256)
        with self.assertRaises((AttributeError, TypeError)):
            frozen.gates[0].expected = 1
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            freeze_regression_gates(
                [
                    {
                        "id": "g",
                        "metric": "m",
                        "operator": "lte",
                        "expected": 1,
                        "decision": "promotion",
                        "threshold_from_samples": True,
                    }
                ]
            )
        with self.assertRaisesRegex(ContractError, "must be promotion"):
            RegressionGateSpec("g", "m", "lte", 1, "evidence")
        with self.assertRaisesRegex(ContractError, "must be finite"):
            RegressionGateSpec("g", "m", "lte", math.nan)
        with self.assertRaisesRegex(ContractError, "must be unique"):
            freeze_regression_gates(
                [
                    RegressionGateSpec("g", "a", "lte", 1),
                    RegressionGateSpec("g", "b", "gte", 1),
                ]
            )

    def test_exact_thresholds_pass_promotion(self):
        result = evaluate_regression_gates(
            self.gates(),
            {
                "ttft_increase_ratio": 0.10,
                "throughput_ratio": 0.95,
                "new_output_violation_count": 0,
            },
        )
        self.assertEqual(result.evidence_status, "publishable")
        self.assertEqual(result.promotion_status, "passed")
        self.assertEqual(result.promotion_reasons, ())
        self.assertTrue(all(gate.status == "passed" for gate in result.gate_results))

    def test_one_past_thresholds_fail_promotion_but_remain_publishable(self):
        result = evaluate_regression_gates(
            self.gates(),
            {
                "ttft_increase_ratio": math.nextafter(0.10, math.inf),
                "throughput_ratio": math.nextafter(0.95, -math.inf),
                "new_output_violation_count": 1,
            },
        )
        self.assertEqual(result.evidence_status, "publishable")
        self.assertEqual(result.promotion_status, "failed")
        self.assertEqual(
            result.promotion_reasons,
            (
                "promotion_gate_failed:ttft-increase",
                "promotion_gate_failed:throughput-ratio",
                "promotion_gate_failed:new-output-violations",
            ),
        )
        self.assertTrue(all(gate.status == "failed" for gate in result.gate_results))

    def test_missing_or_non_finite_observation_fails_closed(self):
        with self.assertRaisesRegex(ContractError, "is missing"):
            evaluate_regression_gates(self.gates(), {})
        with self.assertRaisesRegex(ContractError, "must be finite"):
            evaluate_regression_gates(
                self.gates(),
                {
                    "ttft_increase_ratio": math.inf,
                    "throughput_ratio": 1,
                    "new_output_violation_count": 0,
                },
            )


if __name__ == "__main__":
    unittest.main()
