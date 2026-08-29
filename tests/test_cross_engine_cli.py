from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict

from turnvector_benchmark.cli import _parser, main
from turnvector_benchmark.cross_engine.native import NativeInferencePlan


ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profiles" / "cross-engine-openai-serving-v1.json"
TARGET = ROOT / "targets" / "ax-engine-openai-v1.json"
D = "d" * 64


def evidence(target_id: str, value: float, *, pairing_id: str = "pair-1") -> Dict[str, Any]:
    return {
        "schema_version": "turnvector.benchmark.cross-engine-evidence.v1",
        "campaign_id": "campaign-1",
        "profile": {"id": "cross-engine-openai-serving-v1", "sha256": "1" * 64},
        "scenario_sets": [
            {"id": "openai-serving-common-v1", "sha256": "2" * 64}
        ],
        "target": {"id": target_id, "sha256": "3" * 64},
        "session": {
            "id": "session-1",
            "benchmark_revision": "4" * 40,
            "benchmark_dirty": False,
            "target_revision_before": "5" * 40,
            "target_revision_after": "5" * 40,
            "target_dirty_before": False,
            "target_dirty_after": False,
            "started_at": "2026-08-29T00:00:00Z",
            "finished_at": "2026-08-29T00:01:00Z",
            "host_fingerprint_sha256": "6" * 64,
        },
        "case_plan_sha256": "7" * 64,
        "statuses": {
            "contract_status": "valid",
            "capability_status": "supported",
            "execution_status": "completed",
            "evidence_status": "publishable",
            "promotion_status": "not_applicable",
            "coverage_status": "complete",
        },
        "rows": [
            {
                "case_id": "case-1",
                "pairing_id": pairing_id,
                "metric_id": "ttft_ms",
                "unit": "milliseconds",
                "value": value,
                "eligibility": "available",
                "measurement_surface": "openai_serving",
                "comparison_form": "absolute",
                "semantic_claim": "serving",
                "observation_level": "client_only",
                "provenance_class": "benchmark_measurement",
                "workload_contract": "same_api_workload",
                "model_equivalence_class": "shape_matched",
                "raw_artifact_ids": ["raw-trials"],
            }
        ],
        "artifacts": {"id": "artifact-manifest", "sha256": "8" * 64},
    }


class CrossEngineCLITests(unittest.TestCase):
    def test_public_commands_exist_without_a_manifest_command(self) -> None:
        choices = _parser()._subparsers._group_actions[0].choices
        for command in (
            "inspect-cross-engine",
            "run-cross-engine",
            "compare-cross-engine",
            "promote-cross-engine-baseline",
            "inspect-cross-engine-native",
            "validate-cross-engine-native",
        ):
            self.assertIn(command, choices)
        self.assertFalse(any("manifest" in command for command in choices))

    def test_inspect_is_side_effect_free_and_reports_frozen_plan(self) -> None:
        before = PROFILE.stat().st_mtime_ns
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["inspect-cross-engine", "--profile", str(PROFILE)])
        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["scenario_count"], 13)
        self.assertEqual(report["planned_case_count"], 132)
        self.assertEqual(
            report["case_plan_sha256"],
            "bb3073ba1dd5ee7413770fc559dc7b03fa60df6e59f997b7884a04c0ebdec699",
        )
        self.assertEqual(PROFILE.stat().st_mtime_ns, before)

    def test_run_freezes_campaign_before_refusing_disabled_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "run-cross-engine",
                        "--profile",
                        str(PROFILE),
                        "--target",
                        str(TARGET),
                        "--output",
                        str(output),
                    ]
                )
            report = json.loads(stdout.getvalue())
            self.assertEqual(code, 4)
            self.assertEqual(report["status"], "preflight_only")
            self.assertEqual(report["execution_status"], "not_started")
            self.assertEqual(report["evidence_status"], "not_evaluated")
            campaign = json.loads((output / "campaign.json").read_text(encoding="utf-8"))
            self.assertEqual(len(campaign["case_plan"]), 132)
            self.assertEqual(len(campaign["campaign"]["cells"]), 132)
            self.assertNotIn("metrics", campaign)
            self.assertFalse((output / "report.json").exists())

    def test_run_rejects_repository_and_nonempty_output_roots(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(
                [
                    "run-cross-engine",
                    "--profile",
                    str(PROFILE),
                    "--target",
                    str(TARGET),
                    "--output",
                    str(ROOT / "forbidden-evidence"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("outside the repository", stderr.getvalue())
        self.assertFalse((ROOT / "forbidden-evidence").exists())

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            output.mkdir()
            (output / "occupied").write_text("x", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(
                    [
                        "run-cross-engine",
                        "--profile",
                        str(PROFILE),
                        "--target",
                        str(TARGET),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("absent or empty", stderr.getvalue())
            self.assertEqual([path.name for path in output.iterdir()], ["occupied"])

    def test_compare_validates_each_evidence_and_requires_common_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for target_id, value in (("left-target", 10.0), ("right-target", 20.0)):
                directory = root / target_id
                directory.mkdir()
                (directory / "evidence.json").write_text(
                    json.dumps(evidence(target_id, value)), encoding="utf-8"
                )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "compare-cross-engine",
                        "--evidence",
                        str(root),
                        "--require-common-core",
                    ]
                )
            report = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["evidence_count"], 2)
            self.assertEqual(report["pairs"][0]["coverage"]["coverage_status"], "complete")
            self.assertEqual(
                report["pairs"][0]["comparison_rows"][0]["ratio_right_over_left"],
                2.0,
            )
            self.assertIsNone(report["qualification_claim"])

            right = root / "right-target" / "evidence.json"
            invalid = json.loads(right.read_text(encoding="utf-8"))
            invalid["unknown"] = True
            right.write_text(json.dumps(invalid), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["compare-cross-engine", "--evidence", str(root)])
            self.assertEqual(code, 2)
            self.assertIn("unknown field", stderr.getvalue())

    def test_compare_rejects_zero_common_cells_only_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = (
                evidence("left-target", 10.0, pairing_id="left-pair"),
                evidence("right-target", 20.0, pairing_id="right-pair"),
            )
            for value in values:
                directory = root / value["target"]["id"]
                directory.mkdir()
                (directory / "evidence.json").write_text(
                    json.dumps(value), encoding="utf-8"
                )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["compare-cross-engine", "--evidence", str(root)])
            self.assertEqual(code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(
                report["pairs"][0]["coverage"]["coverage_status"],
                "zero_common_cells",
            )

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(
                    [
                        "compare-cross-engine",
                        "--evidence",
                        str(root),
                        "--require-common-core",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("zero common cells", stderr.getvalue())

    def test_baseline_promotion_is_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            (evidence_root / "raw.jsonl").write_text("{}\n", encoding="utf-8")
            registry = root / "registry"
            argv = [
                "promote-cross-engine-baseline",
                "--registry",
                str(registry),
                "--baseline-id",
                "baseline-v1",
                "--evidence",
                str(evidence_root),
                "--authority-id",
                "release-owner",
                "--profile-sha256",
                D,
                "--scenario-set-sha256",
                D,
                "--target-sha256",
                D,
                "--model-sha256",
                D,
                "--physical-host-sha256",
                D,
                "--promoted-at",
                "2026-08-29T00:00:00Z",
            ]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(argv)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "created")
            receipt = registry / "baseline-v1" / "receipt.json"
            original = receipt.read_bytes()

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(argv)
            self.assertEqual(code, 2)
            self.assertIn("cannot be overwritten", stderr.getvalue())
            self.assertEqual(receipt.read_bytes(), original)

    def test_native_profile_inspection_and_trial_validation_are_separate(self) -> None:
        plan = NativeInferencePlan(
            "native-plan",
            "mlx-lm-benchmark",
            D,
            "e" * 64,
            (128,),
            16,
            1,
            2,
            0,
            "greedy",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "native.json"
            profile.write_text(json.dumps(plan.as_dict()), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    ["inspect-cross-engine-native", "--profile", str(profile)]
                )
            report = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["planned_trial_count"], 2)
            self.assertEqual(report["measurement_surface"], "native_inference")

            trials = root / "trials.jsonl"
            records = [
                {
                    "case_id": f"native-r{repetition}",
                    "repetition": repetition,
                    "prompt_tokens": 128,
                    "output_tokens": 16,
                    "prefill_seconds": 0.128,
                    "decode_seconds": 0.1,
                    "output_sha256": D,
                }
                for repetition in (1, 2)
            ]
            trials.write_text(
                "".join(json.dumps(value) + "\n" for value in records),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "validate-cross-engine-native",
                        "--profile",
                        str(profile),
                        "--trials",
                        str(trials),
                    ]
                )
            report = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["trial_count"], 2)
            self.assertEqual(
                report["summary"]["measurement_surface"], "native_inference"
            )


if __name__ == "__main__":
    unittest.main()
