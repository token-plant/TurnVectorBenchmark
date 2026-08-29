"""Static Slice E/F contracts for reports, baselines, and native inference."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any, Dict, Mapping

from tests.test_cross_engine_contracts import (
    DRAFT_2020_12,
    ValidationError,
    assert_schema_closed,
    load_json,
    validate,
)
from turnvector_benchmark.cross_engine.baseline import BaselineReceipt
from turnvector_benchmark.cross_engine.native import NativeInferencePlan
from turnvector_benchmark.cross_engine.reporting import StatusAxes, build_report


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
NATIVE_PROFILE_PATH = ROOT / "profiles" / "cross-engine-native-inference-v1.json"
SERVING_PROFILE_PATH = ROOT / "profiles" / "cross-engine-openai-serving-v1.json"
SCHEMA_NAMES = (
    "cross-engine-report-v1.schema.json",
    "cross-engine-baseline-receipt-v1.schema.json",
    "cross-engine-native-profile-v1.schema.json",
)
D = "a" * 64


class CrossEngineNativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            name: load_json(SCHEMA_DIR / name)
            for name in SCHEMA_NAMES
        }
        cls.report_schema = cls.schemas[SCHEMA_NAMES[0]]
        cls.baseline_schema = cls.schemas[SCHEMA_NAMES[1]]
        cls.native_schema = cls.schemas[SCHEMA_NAMES[2]]
        cls.native_profile = load_json(NATIVE_PROFILE_PATH)
        cls.serving_profile = load_json(SERVING_PROFILE_PATH)
        cls.serving_schema = load_json(SCHEMA_DIR / "cross-engine-profile-v1.schema.json")

    def assert_invalid(self, instance: Any, schema: Mapping[str, Any]) -> None:
        with self.assertRaises(ValidationError):
            validate(instance, schema)

    def test_new_schemas_are_draft_2020_12_and_recursively_closed(self) -> None:
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                self.assertEqual(schema["$schema"], DRAFT_2020_12)
                self.assertEqual(
                    schema["$id"],
                    "https://token-plant.github.io/TurnVectorBenchmark/schemas/{}".format(name),
                )
                assert_schema_closed(self, schema, schema)

    def test_runtime_report_and_baseline_receipt_validate(self) -> None:
        axes = StatusAxes(
            "valid", "supported", "completed", "publishable", "not_applicable", "complete"
        )
        report = build_report(
            "run-1", axes, (), coverage={"coverage_status": "complete"}
        )
        validate(report, self.report_schema)

        receipt = BaselineReceipt(
            "release-authority",
            "2026-08-28T00:00:00Z",
            D,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            "f" * 64,
            None,
        ).as_dict()
        validate(receipt, self.baseline_schema)

        bad_timestamp = copy.deepcopy(receipt)
        bad_timestamp["promoted_at"] = "2026-08-28T00:00:00"
        self.assert_invalid(bad_timestamp, self.baseline_schema)

    def test_native_profile_validates_and_matches_runtime_plan_projection(self) -> None:
        validate(self.native_profile, self.native_schema)
        fields: Dict[str, Any] = {
            key: value
            for key, value in self.native_profile.items()
            if key not in {"schema_version", "measurement_surface"}
        }
        fields["prompt_tokens"] = tuple(fields["prompt_tokens"])
        plan = NativeInferencePlan(**fields)
        self.assertEqual(plan.as_dict(), self.native_profile)
        self.assertEqual(plan.command_prefix, ("python3", "-m", "mlx_lm.benchmark"))

    def test_unknown_missing_mistyped_and_forbidden_qualification_fields_fail(self) -> None:
        axes = StatusAxes(
            "valid", "supported", "completed", "publishable", "not_applicable", "complete"
        )
        samples = (
            (
                build_report("run-1", axes, (), coverage={"coverage_status": "complete"}),
                self.report_schema,
                "statuses",
            ),
            (
                BaselineReceipt(
                    "authority", "2026-08-28T00:00:00Z", D, D, D, D, D, D, None
                ).as_dict(),
                self.baseline_schema,
                "profile_sha256",
            ),
            (self.native_profile, self.native_schema, "measurement_surface"),
        )
        for original, schema, required_field in samples:
            with self.subTest(field=required_field):
                unknown = copy.deepcopy(original)
                unknown["unexpected"] = True
                self.assert_invalid(unknown, schema)

                missing = copy.deepcopy(original)
                del missing[required_field]
                self.assert_invalid(missing, schema)

                mistyped = copy.deepcopy(original)
                mistyped[required_field] = 1
                self.assert_invalid(mistyped, schema)

        for forbidden in ("full_implementation_status", "qualification_lanes", "data_plane"):
            report = build_report("run-1", axes, (), coverage={"coverage_status": "complete"})
            report[forbidden] = "passed"
            self.assert_invalid(report, self.report_schema)

    def test_native_and_serving_measurement_surfaces_cannot_cross_or_mix(self) -> None:
        wrong_native = copy.deepcopy(self.native_profile)
        wrong_native["measurement_surface"] = "openai_serving"
        self.assert_invalid(wrong_native, self.native_schema)

        wrong_serving = copy.deepcopy(self.serving_profile)
        wrong_serving["measurement_surface"] = "native_inference"
        self.assert_invalid(wrong_serving, self.serving_schema)

        def row(surface: str, case_id: str) -> Dict[str, Any]:
            return {
                "case_id": case_id,
                "pairing_id": "pair-1",
                "metric_id": "throughput",
                "unit": "tokens_per_second",
                "value": 1.0,
                "eligibility": "available",
                "measurement_surface": surface,
                "comparison_form": "absolute",
                "semantic_claim": "serving",
                "observation_level": "benchmark_forced",
                "provenance_class": "benchmark_measurement",
                "workload_contract": "same_resolved_model_work",
                "model_equivalence_class": "exact_artifact",
                "raw_artifact_ids": ["raw-trials"],
            }

        axes = StatusAxes(
            "valid", "supported", "completed", "publishable", "not_applicable", "complete"
        )
        native_report = build_report(
            "native-run",
            axes,
            (),
            coverage={"coverage_status": "complete"},
            metric_rows=(row("native_inference", "native-case"),),
        )
        validate(native_report, self.report_schema)

        mixed_report = copy.deepcopy(native_report)
        mixed_report["metric_rows"].append(row("openai_serving", "serving-case"))
        self.assert_invalid(mixed_report, self.report_schema)


if __name__ == "__main__":
    unittest.main()
