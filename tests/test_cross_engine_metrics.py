from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.metrics import (
    RequestObservation,
    failed_observation,
    nearest_rank,
    reduce_request_metrics,
    reduce_trial_metrics,
    summarize_observations,
)


class RequestReducerTests(unittest.TestCase):
    def observation(self, **overrides):
        values = {
            "request_id": "request-1",
            "dispatch_ns": 0,
            "terminal_ns": 50_000_000,
            "content_event_ns": (10_000_000, 30_000_000),
            "wire_complete": True,
            "benchmark_input_tokens": 4,
            "benchmark_output_tokens": 3,
            "server_input_tokens": 4,
            "server_output_tokens": 3,
            "token_authority": "both",
            "required_output_tokens": 3,
            "output_obligations_met": True,
            "error_class": None,
            "slo_e2e_ms": 50.0,
        }
        values.update(overrides)
        return RequestObservation(**values)

    def test_exact_controlled_clock_boundaries(self) -> None:
        metrics = reduce_request_metrics(self.observation())
        self.assertEqual(metrics.ttft_ms, 10.0)
        self.assertEqual(metrics.e2e_ms, 50.0)
        self.assertEqual(metrics.stream_event_interval_ms, (20.0,))
        self.assertEqual(metrics.client_post_first_output_ms_per_token, 10.0)
        self.assertEqual(metrics.effective_prefill_tokens_per_second, 400.0)
        self.assertTrue(metrics.completion_valid)
        self.assertTrue(metrics.slo_satisfied)

    def test_zero_one_and_coalesced_output_eligibility(self) -> None:
        one = reduce_request_metrics(
            self.observation(
                content_event_ns=(10_000_000,),
                benchmark_output_tokens=1,
                server_output_tokens=1,
                required_output_tokens=1,
            )
        )
        self.assertIsNone(one.client_post_first_output_ms_per_token)
        coalesced = reduce_request_metrics(
            self.observation(
                content_event_ns=(10_000_000,),
                benchmark_output_tokens=3,
                server_output_tokens=3,
            )
        )
        self.assertEqual(coalesced.client_post_first_output_ms_per_token, 0.0)
        empty = reduce_request_metrics(
            self.observation(
                content_event_ns=(),
                benchmark_output_tokens=0,
                server_output_tokens=0,
            )
        )
        self.assertFalse(empty.completion_valid)
        self.assertIsNone(empty.ttft_ms)
        self.assertIn("output_contract", empty.error_classes)

    def test_token_authority_disagreement_fails_contract(self) -> None:
        with self.assertRaisesRegex(ContractError, "disagree"):
            reduce_request_metrics(self.observation(server_output_tokens=2))
        with self.assertRaisesRegex(ContractError, "requires complete server usage"):
            reduce_request_metrics(
                self.observation(
                    token_authority="server_usage",
                    server_output_tokens=None,
                )
            )

    def test_early_eos_is_retained_as_output_contract_failure(self) -> None:
        metrics = reduce_request_metrics(
            self.observation(
                benchmark_output_tokens=2,
                server_output_tokens=2,
                required_output_tokens=3,
            )
        )
        self.assertFalse(metrics.output_contract_ok)
        self.assertFalse(metrics.completion_valid)
        self.assertIn("output_contract", metrics.error_classes)
        self.assertEqual(metrics.e2e_ms, 50.0)

    def test_clock_order_and_missing_terminal_fail_closed(self) -> None:
        with self.assertRaisesRegex(ContractError, "monotone"):
            reduce_request_metrics(
                self.observation(content_event_ns=(30_000_000, 10_000_000))
            )
        with self.assertRaisesRegex(ContractError, "terminal"):
            reduce_request_metrics(self.observation(terminal_ns=None))


class TrialAndAggregateReducerTests(unittest.TestCase):
    def valid(self):
        return reduce_request_metrics(
            RequestObservation(
                request_id="valid",
                dispatch_ns=0,
                terminal_ns=500_000_000,
                content_event_ns=(100_000_000, 300_000_000),
                wire_complete=True,
                benchmark_input_tokens=4,
                benchmark_output_tokens=3,
                server_input_tokens=None,
                server_output_tokens=None,
                token_authority="benchmark_tokenizer",
                required_output_tokens=3,
                slo_e2e_ms=600.0,
            )
        )

    def test_complete_interval_and_offered_denominators(self) -> None:
        valid = self.valid()
        failed = reduce_request_metrics(
            failed_observation(
                "failed",
                dispatch_ns=1_000_000_000,
                error_class="timeout",
                benchmark_input_tokens=4,
                required_output_tokens=3,
                slo_e2e_ms=600,
            )
        )
        trial = reduce_trial_metrics(
            [valid, failed],
            t0_ns=0,
            t1_ns=2_000_000_000,
            planned_arrival_count=2,
            planned_arrival_interval_ns=1_000_000_000,
        )
        self.assertEqual(trial.request_throughput, 0.5)
        self.assertEqual(trial.output_throughput, 1.5)
        self.assertEqual(trial.offered_request_rate, 2.0)
        self.assertEqual(trial.slo_goodput_ratio, 0.5)
        self.assertEqual(trial.error_count, 1)
        self.assertEqual(trial.error_counts, {"timeout": 1, "output_contract": 1, "output_obligation": 1})

    def test_missing_offered_attempt_and_duplicate_id_fail_closed(self) -> None:
        valid = self.valid()
        with self.assertRaisesRegex(ContractError, "every planned"):
            reduce_trial_metrics(
                [valid],
                t0_ns=0,
                t1_ns=1,
                planned_arrival_count=2,
                planned_arrival_interval_ns=1,
            )
        with self.assertRaisesRegex(ContractError, "duplicate"):
            reduce_trial_metrics(
                [valid, valid],
                t0_ns=0,
                t1_ns=1,
                planned_arrival_count=2,
                planned_arrival_interval_ns=1,
            )

    def test_nearest_rank_and_complete_summary(self) -> None:
        values = list(range(1, 21))
        self.assertEqual(nearest_rank(values, 50), 10.0)
        self.assertEqual(nearest_rank(values, 95), 19.0)
        self.assertEqual(nearest_rank(values, 99), 20.0)
        summary = summarize_observations(values, unavailable_count=2)
        self.assertEqual(summary["count"], 20)
        self.assertEqual(summary["unavailable_count"], 2)
        self.assertEqual(summary["sum"], 210.0)
        self.assertEqual(summary["mean"], 10.5)
        empty = summarize_observations([], unavailable_count=3)
        self.assertEqual(empty["count"], 0)
        self.assertIsNone(empty["p50"])
        self.assertEqual(empty["unavailable_count"], 3)


if __name__ == "__main__":
    unittest.main()
