from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.campaign import (
    AttemptLedger,
    balanced_target_orders,
    freeze_campaign,
)


class CrossEngineCampaignTests(unittest.TestCase):
    def temporary_path(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_balanced_orders_are_canonical_and_position_balanced(self) -> None:
        orders = balanced_target_orders(["turnvector", "ax-engine", "mlx-lm"], 3)
        self.assertEqual(
            orders,
            (
                ("ax-engine", "mlx-lm", "turnvector"),
                ("mlx-lm", "turnvector", "ax-engine"),
                ("turnvector", "ax-engine", "mlx-lm"),
            ),
        )
        for position in range(3):
            self.assertEqual({order[position] for order in orders}, set(orders[0]))

    def test_campaign_freezes_pairing_before_target_start(self) -> None:
        plan = freeze_campaign(
            campaign_id="campaign-v1",
            cases=[
                {
                    "scenario_id": "single-client-streaming",
                    "case_id": "short",
                    "required_capabilities": [],
                },
                {
                    "scenario_id": "long-context-serving",
                    "case_id": "context-1k",
                    "required_capabilities": ["process_memory_attribution"],
                },
            ],
            target_ids=["turnvector", "mlx-lm"],
            repetition_count=2,
        )
        self.assertEqual(len(plan.cells), 8)
        self.assertEqual(plan.target_orders[0], ("mlx-lm", "turnvector"))
        self.assertEqual(plan.target_orders[1], ("turnvector", "mlx-lm"))
        paired = [cell for cell in plan.cells if cell.pairing_key.endswith("r0000")]
        self.assertEqual({cell.target_id for cell in paired[:2]}, {"mlx-lm", "turnvector"})
        self.assertEqual([cell.ordinal for cell in plan.cells], list(range(8)))

    def test_campaign_retains_stable_immutable_matrix_parameters(self) -> None:
        parameters = {"offered_load_rps": 4, "prompt_bucket": "medium"}
        plan = freeze_campaign(
            campaign_id="campaign-parameters",
            cases=[
                {
                    "scenario_id": "open-loop-load-sweep",
                    "case_id": "openai-serving.open-loop-load-sweep.workload.c0002",
                    "pairing_id": "openai-serving.open-loop-load-sweep.workload.c0002",
                    "matrix_id": "workload",
                    "parameters": parameters,
                }
            ],
            target_ids=["mlx-lm"],
            repetition_count=1,
        )
        parameters["offered_load_rps"] = 99
        cell = plan.cells[0]
        self.assertEqual(cell.matrix_id, "workload")
        self.assertEqual(cell.pairing_id, "openai-serving.open-loop-load-sweep.workload.c0002")
        self.assertEqual(dict(cell.parameters), {"offered_load_rps": 4, "prompt_bucket": "medium"})
        with self.assertRaises(TypeError):
            cell.parameters["offered_load_rps"] = 8
        self.assertEqual(
            cell.as_dict()["parameters"],
            {"offered_load_rps": 4, "prompt_bucket": "medium"},
        )

    def test_campaign_rejects_nested_or_nonfinite_parameters(self) -> None:
        for value in ({"x": []}, {"x": float("nan")}, {"x": None}):
            with self.subTest(value=value), self.assertRaises(ContractError):
                freeze_campaign(
                    campaign_id="campaign-invalid-parameters",
                    cases=[{"scenario_id": "scenario-a", "case_id": "case-a", "parameters": value}],
                    target_ids=["mlx-lm"],
                    repetition_count=1,
                )

    def test_attempt_ledger_retains_invalid_retry_and_first_eligible(self) -> None:
        root = self.temporary_path()
        ledger = AttemptLedger(
            root / "attempts.jsonl",
            retryable_reason_codes=["host_load_transient"],
        )
        first = ledger.append(
            cell_id="cell-a",
            status="environment_invalid",
            reason_code="host_load_transient",
            started_monotonic_ns=10,
            finished_monotonic_ns=20,
        )
        self.assertTrue(ledger.can_retry(first))
        second = ledger.append(
            cell_id="cell-a",
            status="completed",
            reason_code=None,
            retry_of=first.attempt_ordinal,
            started_monotonic_ns=30,
            finished_monotonic_ns=40,
        )
        self.assertEqual(ledger.primary_attempt("cell-a"), second)
        rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
        self.assertEqual([row["attempt_ordinal"] for row in rows], [0, 1])
        self.assertFalse(rows[0]["eligible_for_primary"])
        self.assertTrue(rows[1]["eligible_for_primary"])

    def test_performance_failure_cannot_be_retried(self) -> None:
        root = self.temporary_path()
        ledger = AttemptLedger(root / "attempts.jsonl", retryable_reason_codes=["host_load_transient"])
        first = ledger.append(
            cell_id="cell-a",
            status="timeout",
            reason_code="request_timeout",
            started_monotonic_ns=1,
            finished_monotonic_ns=2,
        )
        self.assertFalse(ledger.can_retry(first))
        with self.assertRaisesRegex(ContractError, "new attempt is allowed only"):
            ledger.append(
                cell_id="cell-a",
                status="completed",
                reason_code=None,
                retry_of=0,
                started_monotonic_ns=3,
                finished_monotonic_ns=4,
            )


if __name__ == "__main__":
    unittest.main()
