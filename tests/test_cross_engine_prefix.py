from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.prefix import (
    PREFIX_STATES,
    PrefixStateRow,
    RestartProof,
    summarize_configured_reuse,
    validate_prefix_state_rows,
)
from turnvector_benchmark.cross_engine.routes import (
    NativeRouteRecord,
    RouteCapture,
    build_route_evidence,
)


class CrossEnginePrefixTests(unittest.TestCase):
    def route(self, request_id: str, cache="memory_prefix", corroborated=True):
        record = {"backend": "mlx", "execution": "direct", "cache": cache,
                  "model_state": "resident", "fallback": "none"}
        return build_route_evidence(
            request_id,
            RouteCapture(
                NativeRouteRecord(request_id, record),
                process_identity_verified=corroborated,
                config_identity_verified=corroborated,
                route_specific_corroboration=corroborated,
            ),
        )

    def row(self, request_id="r", *, state="warm_memory_prefix_hit", hit=128,
            minimum=0.75, cache="memory_prefix", corroborated=True,
            restart_proof=None, latency_ms=10.0):
        return PrefixStateRow(
            request_id=request_id, state=state, configured_reuse=True,
            eligible_prefix_tokens=128, hit_prefix_tokens=hit, block_size_tokens=64,
            minimum_reuse_coverage=minimum, output_hash="same", cold_output_hash="same",
            route=self.route(request_id, cache, corroborated), identities_unchanged=True,
            restart_proof=restart_proof, latency_ms=latency_ms,
        )

    def test_minimum_coverage_uses_complete_rounded_blocks(self):
        partial = self.row(hit=127)
        self.assertEqual(partial.rounded_hit_tokens, 64)
        self.assertEqual(partial.rounded_reuse_coverage, 0.5)
        self.assertFalse(partial.positive_claim)
        self.assertTrue(self.row(hit=128).positive_claim)

    def test_latency_only_or_output_mismatch_cannot_prove_reuse(self):
        fast_declared = self.row(corroborated=False, latency_ms=0.01)
        self.assertFalse(fast_declared.positive_claim)
        mismatch = self.row()
        mismatch = PrefixStateRow(**{
            **mismatch.__dict__, "output_hash": "different"
        })
        self.assertFalse(mismatch.positive_claim)

    def test_configured_aggregate_is_intention_to_treat(self):
        rows = [self.row("hit", hit=128), self.row("partial", hit=64), self.row("miss", hit=0)]
        summary = summarize_configured_reuse(
            rows, planned_request_ids=["hit", "partial", "miss"]
        )
        self.assertEqual(summary.rows, tuple(rows))
        self.assertEqual(summary.planned_requests, 3)
        self.assertEqual(summary.positive_claims, 1)
        self.assertEqual(summary.partial_reuse_requests, 1)
        self.assertEqual(summary.miss_requests, 1)
        self.assertEqual(summary.rounded_reuse_coverage, 0.5)
        self.assertAlmostEqual(summary.mean_latency_ms, 10.0)
        with self.assertRaises(ContractError):
            summarize_configured_reuse(rows[:-1], planned_request_ids=["hit", "partial", "miss"])
        missing_latency = [self.row("hit"), self.row("miss", hit=0, latency_ms=None)]
        self.assertIsNone(summarize_configured_reuse(
            missing_latency, planned_request_ids=["hit", "miss"]
        ).mean_latency_ms)

    def test_disk_restore_requires_new_process_and_no_surviving_local_state(self):
        valid = RestartProof(("pid-1:start-1",), ("pid-2:start-2",), ())
        restored = self.row(state="restarted_disk_prefix_hit", cache="disk_prefix",
                            restart_proof=valid)
        self.assertTrue(restored.positive_claim)
        surviving = RestartProof(("old",), ("new",), ("old-cache",))
        self.assertFalse(self.row(state="restarted_disk_prefix_hit", cache="disk_prefix",
                                  restart_proof=surviving).positive_claim)

    def test_all_five_state_rows_are_required_by_state_validator(self):
        rows = [self.row(str(index), state=state, cache=(
            "disk_prefix" if state == "restarted_disk_prefix_hit" else "memory_prefix"
        )) for index, state in enumerate(PREFIX_STATES)]
        validate_prefix_state_rows(rows)
        with self.assertRaises(ContractError):
            validate_prefix_state_rows(rows[:-1])


if __name__ == "__main__":
    unittest.main()
