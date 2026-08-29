from __future__ import annotations

import copy
import unittest

from tests.fixtures.cross_engine.openai_server import FixtureOpenAIServer
from turnvector_benchmark.cross_engine.campaign import freeze_campaign
from turnvector_benchmark.cross_engine.serving import CommonServingExecutor


def scenario(mode: str):
    return {
        "id": "scenario-a",
        "arrival_trace": {
            "mode": mode,
            "requests_per_trial": 1 if mode == "single" else 4,
            "concurrency": 2,
        },
        "output_work": {"required_output_tokens": 3},
        "request_dialect": {"output_bound_field": "max_tokens"},
        "metric_eligibility": {"token_authority": "both"},
        "protocol": {
            "warmup_repetitions": 0,
            "cooldown_seconds": 0,
            "timeout_seconds": 5,
        },
        "slos": {"e2e_ms": 5000, "ttft_ms": 5000},
    }


def cell(mode: str):
    parameters = {}
    if mode == "closed_loop":
        parameters["concurrency"] = 2
    if mode == "open_loop":
        parameters["offered_load_rps"] = 1000
    return freeze_campaign(
        campaign_id="campaign-a",
        cases=[
            {
                "case_id": "case-a",
                "pairing_id": "pair-a",
                "scenario_id": "scenario-a",
                "matrix_id": "workload",
                "parameters": parameters,
            }
        ],
        target_ids=["target-a"],
        repetition_count=1,
    ).cells[0]


class CommonServingExecutorTests(unittest.TestCase):
    @staticmethod
    def prompt_factory(_cell, _ordinal):
        return (
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        )

    @staticmethod
    def token_counter(_messages, output):
        return 4, 3 if output else 0

    def test_all_common_arrival_modes_emit_benchmark_owned_raw_evidence(self) -> None:
        for mode in ("single", "sequential", "closed_loop", "open_loop"):
            with self.subTest(mode=mode), FixtureOpenAIServer() as server:
                executor = CommonServingExecutor(
                    scenarios={"scenario-a": scenario(mode)},
                    prompt_factory=self.prompt_factory,
                    token_counter=self.token_counter,
                )
                result = executor.run(
                    cell=cell(mode), endpoint=server.endpoint(), collector=object()
                )
                expected = 1 if mode == "single" else 4
                self.assertEqual(result["arrival_mode"], mode)
                self.assertEqual(len(result["request_trace"]), expected)
                self.assertEqual(len(result["request_metrics"]), expected)
                self.assertEqual(len(result["output_hashes"]), expected)
                self.assertGreaterEqual(len(result["stream_events"]), expected * 7)
                self.assertEqual(
                    result["trial_metrics"]["valid_completed_request_count"], expected
                )
                self.assertEqual(result["trial_metrics"]["failed_request_count"], 0)
                self.assertGreater(result["trial_metrics"]["output_throughput"], 0)

    def test_failed_request_is_retained_in_offered_denominator_under_both_authority(self) -> None:
        with FixtureOpenAIServer("http-error") as server:
            value = scenario("single")
            executor = CommonServingExecutor(
                scenarios={"scenario-a": value},
                prompt_factory=self.prompt_factory,
                token_counter=self.token_counter,
            )
            result = executor.run(
                cell=cell("single"), endpoint=server.endpoint(), collector=object()
            )
        self.assertEqual(result["trial_metrics"]["offered_request_count"], 1)
        self.assertEqual(result["trial_metrics"]["failed_request_count"], 1)
        self.assertEqual(result["request_trace"][0]["error_class"], "request_failed")

    def test_ttft_slo_participates_in_goodput(self) -> None:
        value = scenario("single")
        value["slos"]["ttft_ms"] = 0
        with FixtureOpenAIServer() as server:
            result = CommonServingExecutor(
                scenarios={"scenario-a": value},
                prompt_factory=self.prompt_factory,
                token_counter=self.token_counter,
            ).run(cell=cell("single"), endpoint=server.endpoint(), collector=object())
        self.assertEqual(result["trial_metrics"]["slo_goodput_ratio"], 0)


if __name__ == "__main__":
    unittest.main()
