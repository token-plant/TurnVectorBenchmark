from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.multimodel import (
    ModelObservation,
    ModelWorkload,
    MultiModelCell,
    assess_topology_claim,
    summarize_multi_model_cell,
)


class CrossEngineMultiModelTests(unittest.TestCase):
    def cell(self, topology="one-process", *, memory=1024, arrivals_b=(0.0, 500.0),
             cost="cost-v1"):
        return MultiModelCell(
            deployment_topology=topology,
            workloads=(
                ModelWorkload("a", "artifact-a", (0.0, 500.0), "four-tokens", 1.0),
                ModelWorkload("b", "artifact-b", arrivals_b, "four-tokens", 3.0),
            ),
            duration_ms=1000.0, concurrency=2, memory_budget_bytes=memory,
            admission_policy="weighted", scheduler_cost_boundary_identity=cost,
        )

    def test_summary_always_emits_per_model_fairness_and_progress_rows(self):
        summary = summarize_multi_model_cell(
            self.cell(),
            [ModelObservation("a", True, (100.0, 900.0), 20, 1),
             ModelObservation("b", True, (200.0, 400.0), 60, 2)],
        )
        self.assertEqual([row.model_id for row in summary.per_model_rows], ["a", "b"])
        a, b = summary.per_model_rows
        self.assertEqual(a.offered_load_requests_per_second, 2.0)
        self.assertEqual(a.completion_rate, 1.0)
        self.assertEqual(a.maximum_progress_gap_ms, 800.0)
        self.assertEqual(a.useful_service_share, 0.25)
        self.assertEqual(a.target_weighted_service_share, 0.25)
        self.assertEqual(b.weighted_service_error, 0.0)
        self.assertEqual(summary.aggregate_output_throughput_tokens_per_second, 80.0)
        self.assertFalse(summary.failed)

    def test_unavailable_or_starved_configured_model_is_failure_not_omitted(self):
        summary = summarize_multi_model_cell(
            self.cell(), [ModelObservation("a", True, (100.0,), 10, 1)]
        )
        self.assertEqual(len(summary.per_model_rows), 2)
        missing = summary.per_model_rows[1]
        self.assertFalse(missing.available)
        self.assertEqual(missing.completion_rate, 0.0)
        self.assertEqual(missing.maximum_progress_gap_ms, 1000.0)
        self.assertEqual(missing.failure_reasons,
                         ("configured_model_unavailable", "zero_completions"))
        self.assertTrue(summary.failed)

    def test_unknown_or_duplicate_observations_fail_closed(self):
        with self.assertRaises(ContractError):
            summarize_multi_model_cell(
                self.cell(), [ModelObservation("outside", True, (), 0, 0)]
            )
        duplicate = ModelObservation("a", True, (10.0,), 1, 1)
        with self.assertRaises(ContractError):
            summarize_multi_model_cell(self.cell(), [duplicate, duplicate])

    def test_topology_is_row_identity_and_scheduler_claim_has_equivalence_boundary(self):
        one = self.cell("one-process")
        many = self.cell("process-per-model")
        self.assertNotEqual(one.row_identity, many.row_identity)
        eligible = assess_topology_claim(one, many)
        self.assertTrue(eligible.intrinsic_scheduler_claim_eligible)
        self.assertEqual(eligible.left_topology, "one-process")
        self.assertEqual(eligible.right_topology, "process-per-model")

        mismatched = assess_topology_claim(one, self.cell(
            "process-per-model", memory=2048, arrivals_b=(0.0, 600.0), cost=None
        ))
        self.assertFalse(mismatched.intrinsic_scheduler_claim_eligible)
        self.assertEqual(mismatched.claim_kind, "named_topology_only")
        self.assertIn("memory_budget_bytes_mismatch", mismatched.reasons)
        self.assertIn("arrival_trace_mismatch", mismatched.reasons)
        self.assertIn("scheduler_cost_boundary_not_frozen_or_mismatched", mismatched.reasons)


if __name__ == "__main__":
    unittest.main()
