from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.mtp import (
    MTPCounts,
    MTPTrial,
    assess_mtp_comparison,
)
from turnvector_benchmark.cross_engine.routes import (
    NativeRouteRecord,
    RouteCapture,
    build_route_evidence,
)


class CrossEngineMTPTests(unittest.TestCase):
    def route(self, request_id: str, execution: str, fallback="none"):
        record = {"backend": "mlx", "execution": execution, "cache": "none",
                  "model_state": "resident", "fallback": fallback}
        return build_route_evidence(
            request_id,
            RouteCapture(NativeRouteRecord(request_id, record), True, True, True),
        )

    def trial(self, request_id: str, mode: str, *, elapsed=50.0, target="engine-a",
              output_hash="output", counts=None, execution=None, fallback="none",
              error=False):
        direct = mode == "direct"
        return MTPTrial(
            request_id=request_id, mode=mode, target_id=target, model_identity="model-sha",
            prompt_suite_identity="prompts-sha", sampling_identity="greedy-seed-0",
            host_session_id="session-1", fixed_output_tokens=4,
            output_token_hash=output_hash,
            route=self.route(request_id, execution or ("direct" if direct else "mtp"), fallback),
            counts=counts or (MTPCounts(0, 0, 0, 4, 1) if direct
                              else MTPCounts(10, 6, 4, 4, 1)),
            elapsed_ms=elapsed, completed=True, error=error,
            mtp_sidecar_identity=None if direct else "sidecar-sha",
        )

    def assess(self, direct, candidate, **kwargs):
        return assess_mtp_comparison(
            [direct], [candidate], planned_configured_request_ids=[candidate.request_id],
            sampling_mode="greedy", **kwargs
        )

    def test_same_target_reconciled_direct_denominator_allows_pure_speedup(self):
        result = self.assess(self.trial("direct", "direct", elapsed=100),
                             self.trial("mtp", "configured_mtp", elapsed=50))
        self.assertTrue(result.pure_speedup_eligible)
        self.assertEqual(result.pure_mtp_speedup, 2.0)
        self.assertEqual(result.mtp_accept_ratio, 0.6)

    def test_direct_fallback_remains_in_candidate_and_is_not_mtp(self):
        fallback = self.trial("mtp", "configured_mtp", execution="direct",
                              fallback="unsupported")
        result = self.assess(self.trial("direct", "direct"), fallback)
        self.assertEqual(result.configured_trials, (fallback,))
        self.assertEqual(result.fallback_count, 1)
        self.assertIsNone(result.pure_mtp_speedup)
        self.assertIn("fallback_observed", result.pure_speedup_reasons)
        self.assertIn("mtp_route_not_proved", result.pure_speedup_reasons)

    def test_zero_draft_is_unavailable_and_bad_counts_block_speedup(self):
        zero = self.trial("mtp", "configured_mtp", counts=MTPCounts(0, 0, 0, 4, 1))
        result = self.assess(self.trial("direct", "direct"), zero)
        self.assertIsNone(result.mtp_accept_ratio)
        self.assertIn("zero_drafted_tokens", result.pure_speedup_reasons)
        bad = self.trial("mtp", "configured_mtp", counts=MTPCounts(10, 6, 3, 4, 1))
        self.assertIn("token_counts_do_not_reconcile",
                      self.assess(self.trial("direct", "direct"), bad).pure_speedup_reasons)

    def test_output_correctness_sampling_and_same_target_precede_speedup(self):
        candidate = self.trial("mtp", "configured_mtp", target="engine-b",
                               output_hash="wrong")
        result = self.assess(self.trial("direct", "direct"), candidate)
        self.assertIn("direct_denominator_identity_mismatch", result.pure_speedup_reasons)
        self.assertIn("output_token_identity_mismatch", result.pure_speedup_reasons)
        sampled = assess_mtp_comparison(
            [self.trial("direct", "direct")], [self.trial("mtp", "configured_mtp")],
            planned_configured_request_ids=["mtp"], sampling_mode="sampled"
        )
        self.assertIn("sampling_not_reproducibly_coupled", sampled.pure_speedup_reasons)

    def test_planned_candidate_order_and_membership_are_exact(self):
        candidate = self.trial("mtp", "configured_mtp")
        with self.assertRaises(ContractError):
            assess_mtp_comparison([self.trial("direct", "direct")], [candidate],
                                  planned_configured_request_ids=["different"],
                                  sampling_mode="greedy")


if __name__ == "__main__":
    unittest.main()
