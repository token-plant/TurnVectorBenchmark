"""Slice E/F comparison, baseline, and native-inference tests."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import mock

from turnvector_benchmark.authority.compiler_inputs import sha256_bound_bytes
from turnvector_benchmark.cross_engine.baseline import (
    BASELINE_COMMIT_FILE,
    BaselineReceipt,
    baseline_applicability,
    promote_baseline,
)
from turnvector_benchmark.cross_engine.comparison import (
    comparison_summary,
    coverage_intersection,
    paired_rows,
)
from turnvector_benchmark.cross_engine.native import (
    NativeInferencePlan,
    parse_native_trials,
    summarize_native_trials,
)
from turnvector_benchmark.cross_engine.reporting import (
    Diagnostic,
    StatusAxes,
    build_report,
    cross_engine_exit_code,
)
from turnvector_benchmark.core import ContractError


D = "a" * 64


class CrossEngineComparisonTests(unittest.TestCase):
    def test_intersection_discloses_partial_coverage_and_never_imputes(self):
        value = coverage_intersection(
            ["a", "b", "c"],
            {"a": "supported", "b": "supported", "c": "capability_unsupported"},
            {"a": "supported", "b": "profile_incompatible", "c": "supported"},
        )
        self.assertEqual(value.coverage_status, "partial")
        self.assertEqual(value.common, ("a",))
        self.assertEqual(value.left_only, ("b",))
        self.assertEqual(value.right_only, ("c",))
        rows = paired_rows(value, "left", "right", "throughput", {"a": 10}, {"a": 15})
        self.assertEqual(rows[0].ratio_right_over_left, 1.5)
        self.assertEqual(comparison_summary(rows)["aggregate_reducer"],
                         "geometric_mean_of_paired_ratios")

    def test_zero_intersection_and_missing_common_values_fail(self):
        empty = coverage_intersection(["a"], {"a": "supported"}, {"a": "unsupported"})
        self.assertEqual(empty.coverage_status, "zero_common_cells")
        with self.assertRaises(ContractError):
            paired_rows(empty, "left", "right", "throughput", {}, {})
        partial = coverage_intersection(["a", "b"],
                                        {"a": "supported", "b": "supported"},
                                        {"a": "supported", "b": "supported"})
        with self.assertRaises(ContractError):
            paired_rows(partial, "left", "right", "throughput", {"a": 1}, {"a": 1})


class CrossEngineBaselineTests(unittest.TestCase):
    @staticmethod
    def identities():
        return {
            "profile_sha256": D,
            "scenario_set_sha256": "b" * 64,
            "target_sha256": "c" * 64,
            "model_sha256": "d" * 64,
            "physical_host_sha256": "e" * 64,
        }

    @staticmethod
    def evidence(root):
        evidence = root / "evidence"
        evidence.mkdir()
        (evidence / "raw.jsonl").write_text('{"x":1}\n', encoding="utf-8")
        return evidence

    def test_create_only_promotion_and_applicability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "raw.jsonl").write_text('{"x":1}\n', encoding="utf-8")
            identities = {
                "profile_sha256": D,
                "scenario_set_sha256": "b" * 64,
                "target_sha256": "c" * 64,
                "model_sha256": "d" * 64,
                "physical_host_sha256": "e" * 64,
            }
            receipt = promote_baseline(
                root / "registry", "baseline-v1", evidence,
                authority_id="release-authority", identities=identities,
                promoted_at="2026-08-28T00:00:00Z",
            )
            self.assertTrue((root / "registry/baseline-v1/receipt.json").is_file())
            self.assertEqual(
                (root / "registry/baseline-v1" / BASELINE_COMMIT_FILE).read_text(
                    encoding="ascii"
                ),
                receipt.sha256 + "\n",
            )
            applicable, reasons = baseline_applicability(
                receipt, "2026-08-29T00:00:00Z", identities
            )
            self.assertTrue(applicable)
            self.assertEqual(reasons, ())
            with self.assertRaises(ContractError):
                promote_baseline(root / "registry", "baseline-v1", evidence,
                                 authority_id="release-authority", identities=identities)

    def test_late_or_host_mismatched_baseline_is_inapplicable(self):
        receipt = BaselineReceipt(
            "authority", "2026-08-28T00:00:00Z", D, D, "b" * 64,
            "c" * 64, "d" * 64, "e" * 64, None,
        )
        identities = {
            "profile_sha256": D, "scenario_set_sha256": "b" * 64,
            "target_sha256": "c" * 64, "model_sha256": "d" * 64,
            "physical_host_sha256": "f" * 64,
        }
        applicable, reasons = baseline_applicability(
            receipt, "2026-08-27T00:00:00Z", identities
        )
        self.assertFalse(applicable)
        self.assertIn("baseline_not_promoted_before_candidate", reasons)
        self.assertIn("baseline_identity_mismatch:physical_host_sha256", reasons)

    def test_promotion_failure_does_not_expose_partial_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self.evidence(root)
            registry = root / "registry"
            with mock.patch(
                "turnvector_benchmark.cross_engine.baseline.os.rename",
                side_effect=OSError("injected commit failure"),
            ):
                with self.assertRaisesRegex(ContractError, "failed before commit"):
                    promote_baseline(
                        registry,
                        "baseline-v1",
                        evidence,
                        authority_id="release-authority",
                        identities=self.identities(),
                    )
            self.assertFalse((registry / "baseline-v1").exists())
            self.assertEqual(tuple(registry.iterdir()), ())

    def test_concurrent_create_only_promotion_has_one_complete_winner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self.evidence(root)
            registry = root / "registry"
            barrier = Barrier(2)

            def promote():
                barrier.wait()
                try:
                    return promote_baseline(
                        registry,
                        "baseline-v1",
                        evidence,
                        authority_id="release-authority",
                        identities=self.identities(),
                        promoted_at="2026-08-28T00:00:00Z",
                    )
                except ContractError as error:
                    return error

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(executor.map(lambda _: promote(), range(2)))
            receipts = [item for item in results if isinstance(item, BaselineReceipt)]
            errors = [item for item in results if isinstance(item, ContractError)]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("already exists", str(errors[0]))
            self.assertEqual(
                {path.name for path in registry.iterdir()}, {"baseline-v1"}
            )
            target = registry / "baseline-v1"
            self.assertEqual(
                {path.name for path in target.iterdir()},
                {"receipt.json", "inventory.json", BASELINE_COMMIT_FILE},
            )
            self.assertEqual(
                (target / BASELINE_COMMIT_FILE).read_text(encoding="ascii"),
                receipts[0].sha256 + "\n",
            )

    def test_chronology_boundary_and_same_run_denominator_are_rejected(self):
        receipt = BaselineReceipt(
            "authority", "2026-08-28T00:00:00Z", D, D, "b" * 64,
            "c" * 64, "d" * 64, "e" * 64, None,
        )
        applicable, reasons = baseline_applicability(
            receipt,
            "2026-08-28T00:00:00Z",
            self.identities(),
            candidate_evidence_root_sha256=D,
        )
        self.assertFalse(applicable)
        self.assertEqual(
            reasons,
            (
                "baseline_not_promoted_before_candidate",
                "baseline_same_run_denominator",
            ),
        )
        with self.assertRaisesRegex(ContractError, "RFC 3339"):
            baseline_applicability(
                receipt, "2026-08-29 00:00:00Z", self.identities()
            )


class CrossEngineReportingTests(unittest.TestCase):
    def test_promotion_failure_remains_publishable_and_exit_is_optional(self):
        axes = StatusAxes("valid", "supported", "completed", "publishable", "failed", "complete")
        self.assertEqual(cross_engine_exit_code([axes]), 0)
        self.assertEqual(cross_engine_exit_code([axes], require_promotion=True), 5)
        report = build_report("run-1", axes, [
            Diagnostic("cleanup", "cleanup_failed", "cleanup failed"),
            Diagnostic("promotion_gate", "throughput_regressed", "throughput regressed"),
        ], coverage={"coverage_status": "complete"})
        self.assertEqual(report["primary_diagnostic"]["code"], "throughput_regressed")
        self.assertIsNone(report["qualification_claim"])

    def test_status_invariants_and_exit_precedence(self):
        with self.assertRaises(ContractError):
            StatusAxes("invalid", "supported", "partial", "publishable", "not_evaluated", "partial")
        invalid = StatusAxes("invalid", "supported", "partial", "not_evaluated", "not_evaluated", "partial")
        infrastructure = StatusAxes("valid", "supported", "infrastructure_failed", "not_evaluated", "not_evaluated", "partial")
        self.assertEqual(cross_engine_exit_code([infrastructure, invalid]), 2)


class NativeInferenceTests(unittest.TestCase):
    def plan(self):
        return NativeInferencePlan(
            "native-plan", "mlx-lm-benchmark", D, "b" * 64,
            (128, 512), 16, 1, 2, 0, "greedy",
        )

    def test_registered_native_plan_and_complete_ordered_trials(self):
        plan = self.plan()
        self.assertEqual(plan.command_prefix, ("python3", "-m", "mlx_lm.benchmark"))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trials.jsonl"
            records = []
            for prompt in plan.prompt_tokens:
                for repetition in range(1, plan.repetitions + 1):
                    records.append({
                        "case_id": "p%d-r%d" % (prompt, repetition),
                        "repetition": repetition,
                        "prompt_tokens": prompt,
                        "output_tokens": 16,
                        "prefill_seconds": prompt / 1000,
                        "decode_seconds": 0.1,
                        "output_sha256": D,
                    })
            path.write_text("".join(json.dumps(record, sort_keys=True) + "\n"
                                    for record in records), encoding="utf-8")
            trials = parse_native_trials(path, plan)
            summary = summarize_native_trials(trials)
            self.assertEqual(summary["measurement_surface"], "native_inference")
            self.assertTrue(all(row["output_deterministic"] for row in summary["rows"]))

    def test_unregistered_tool_and_reordered_trials_fail(self):
        with self.assertRaises(ContractError):
            NativeInferencePlan("p", "arbitrary-command", D, D, (1,), 1, 0, 1, 0, "greedy")
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trials.jsonl"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ContractError):
                parse_native_trials(path, plan)


if __name__ == "__main__":
    unittest.main()
