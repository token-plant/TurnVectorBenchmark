from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cli import main
from turnvector_benchmark.performance import (
    PerformanceContract,
    PerformanceLane,
    load_performance_contract,
)


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "profiles" / "performance-publication-v1.json"


class PerformancePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_performance_contract(CONTRACT)

    def temporary_root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def write_artifact(
        self,
        lane_id: str,
        *,
        unsupported: bool = False,
        threshold_overrides: Optional[Mapping[str, float]] = None,
    ) -> Tuple[Path, Dict[str, Any], PerformanceLane]:
        lane = self.contract.lane(lane_id)
        root = self.temporary_root()
        required_artifacts = (
            ("run_manifest", "environment")
            if unsupported
            else self.contract.common_required_artifacts + lane.required_artifacts
        )
        thresholds: Dict[str, float] = (
            {}
            if unsupported
            else {
                gate.gate_id: (0.0 if gate.operator == "gte" else 1e12)
                for gate in lane.gates
                if gate.threshold_source == "certification_record"
            }
        )
        if threshold_overrides:
            thresholds.update(threshold_overrides)

        for artifact_id in required_artifacts:
            if artifact_id == "certification_record":
                continue
            artifact_path = root / f"{artifact_id}.json"
            artifact_path.write_text(
                json.dumps({"artifact_id": artifact_id}, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        if "certification_record" in required_artifacts:
            model_manifest = root / "model_manifest.json"
            prompt_manifest = root / "prompt_manifest.json"
            certification = {
                "schema_version": "turnvector.benchmark.performance-certification.v1",
                "id": "fixture-certification-v1",
                "contract": {
                    "id": self.contract.contract_id,
                    "sha256": self.contract.sha256,
                },
                "lane_id": lane_id,
                "issued_at": "2026-08-16T00:00:00Z",
                "expires_at": "2026-08-18T00:00:00Z",
                "identity": {
                    "resolved_runtime_sha256": "3" * 64,
                    "subject_binary_sha256": "4" * 64,
                    "model_manifest_sha256": hashlib.sha256(
                        model_manifest.read_bytes()
                    ).hexdigest(),
                    "prompt_manifest_sha256": hashlib.sha256(
                        prompt_manifest.read_bytes()
                    ).hexdigest(),
                },
                "thresholds": thresholds,
            }
            (root / "certification_record.json").write_text(
                json.dumps(certification, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        descriptors = []
        for artifact_id in required_artifacts:
            artifact_path = root / f"{artifact_id}.json"
            descriptors.append(
                {
                    "id": artifact_id,
                    "path": artifact_path.name,
                    "size": artifact_path.stat().st_size,
                    "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                    "custody": "benchmark",
                }
            )
        artifact_digests = {
            descriptor["id"]: descriptor["sha256"] for descriptor in descriptors
        }
        claim_type = lane.claim_types[0]
        comparison: Any = None
        if claim_type == "paired_delta":
            comparison = {
                "denominator_id": "same-session-reference",
                "denominator_sha256": "1" * 64,
                "pairing_key_sha256": "2" * 64,
                "measurement_semantics": "same_session",
            }
        cases = []
        if not unsupported:
            metric_values = {
                metric.metric_id: self.passing_metric_value(lane, metric.metric_id)
                for metric in lane.metrics
            }
            for planned in self.contract.case_plan(lane_id):
                trials = [
                    {
                        "repetition": repetition,
                        "metrics": dict(metric_values),
                    }
                    for repetition in range(lane.protocol.measured_repetitions)
                ]
                summary = {
                    metric.metric_id: (
                        metric_values[metric.metric_id]
                        * lane.protocol.measured_repetitions
                        if metric.reducer == "sum"
                        else metric_values[metric.metric_id]
                    )
                    for metric in lane.metrics
                }
                cases.append(
                    {
                        "case_id": planned.case_id,
                        "matrix_id": planned.matrix_id,
                        "parameters": dict(planned.parameters),
                        "status": "measured",
                        "trials": trials,
                        "summary": summary,
                    }
                )
        promotion_gates = [gate for gate in lane.gates if gate.decision == "promotion"]
        publication = (
            {
                "evidence_status": "unsupported",
                "promotion_status": "not_evaluated",
                "candidate": False,
                "evidence_reasons": ["capability_unsupported"],
                "promotion_reasons": [],
                "supersedes": [],
            }
            if unsupported
            else {
                "evidence_status": "publishable",
                "promotion_status": "passed" if promotion_gates else "not_applicable",
                "candidate": True,
                "evidence_reasons": [],
                "promotion_reasons": [],
                "supersedes": [],
            }
        )
        value = {
            "schema_version": "turnvector.benchmark.performance-evidence.v1",
            "contract": {
                "id": self.contract.contract_id,
                "sha256": self.contract.sha256,
            },
            "lane_id": lane_id,
            "support": {
                "status": "unsupported" if unsupported else "supported",
                "reason": "subject does not expose this capability" if unsupported else None,
            },
            "session": {
                "id": "fixture-session-v1",
                "mode": lane.session_mode,
                "claim_type": claim_type,
                "subject_kind": "implementation",
                "started_at": "2026-08-17T00:00:00Z",
                "finished_at": "2026-08-17T00:01:00Z",
                "source_revision": self.contract.source_revision,
                "source_dirty": False,
                "benchmark_revision": "a" * 40,
                "benchmark_dirty": False,
                "comparison": comparison,
            },
            "environment": {
                "device_class": "test-host",
                "memory_bytes": 1048576,
                "os_build": "fixture",
                "resolved_runtime_sha256": "3" * 64,
                "subject_binary_sha256": "4" * 64,
                "model_manifest_sha256": artifact_digests.get(
                    "model_manifest", "5" * 64
                ),
                "prompt_manifest_sha256": artifact_digests.get(
                    "prompt_manifest", "6" * 64
                ),
                "certification_record_sha256": artifact_digests.get(
                    "certification_record", "7" * 64
                ),
                "power_source": "external",
                "thermal_state_start": "nominal",
                "thermal_state_end": "nominal",
                "load_average_1m_start": 0.1,
                "load_average_1m_end": 0.1,
                "top_process_cpu_percent_start": 0.0,
                "top_process_cpu_percent_end": 0.0,
                "host_admission_passed": True,
            },
            "custody": list(self.contract.benchmark_custody),
            "protocol": lane.protocol.as_dict(),
            "frozen_thresholds": thresholds,
            "cases": cases,
            "artifacts": descriptors,
            "publication": publication,
        }
        path = root / "evidence.json"
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path, value, lane

    @staticmethod
    def passing_metric_value(lane: PerformanceLane, metric_id: str) -> float:
        gates = [gate for gate in lane.gates if gate.metric == metric_id]
        if any(
            gate.threshold_source == "benchmark_contract"
            and gate.operator == "eq"
            and gate.expected == 0
            for gate in gates
        ):
            return 0.0
        if any(gate.operator == "lte" for gate in gates):
            return 1.0
        return 100.0

    @staticmethod
    def rewrite(path: Path, value: Mapping[str, Any]) -> None:
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_contract_expands_all_publication_workloads(self) -> None:
        report = self.contract.inspect()
        self.assertEqual(report["lane_count"], 11)
        self.assertEqual(report["core_lane_count"], 9)
        self.assertEqual(report["capability_conditioned_lane_count"], 2)
        self.assertEqual(report["planned_case_count"], 103)
        self.assertEqual(report["metric_count"], 66)
        self.assertEqual(report["gate_count"], 54)
        self.assertEqual(
            report["host_admission_limits"],
            {
                "max_load_average_1m": 2.0,
                "max_top_process_cpu_percent": 50.0,
            },
        )
        self.assertEqual(
            len(self.contract.case_plan("real-model-direct-performance")), 12
        )
        self.assertEqual(len(self.contract.case_plan("embedding-ingest")), 24)

    def test_complete_supported_artifact_is_recomputed_and_publishable(self) -> None:
        path, _, lane = self.write_artifact("online-serving-single-client")
        report = self.contract.validate_artifact(path)
        self.assertEqual(report["status"], "publishable")
        self.assertEqual(report["promotion_status"], "passed")
        self.assertTrue(report["publication_candidate"])
        self.assertEqual(report["case_count"], 8)
        self.assertEqual(
            report["trial_count"], 8 * lane.protocol.measured_repetitions
        )
        self.assertEqual(report["artifact_count"], 10)

    def test_case_plan_and_raw_trial_completeness_fail_closed(self) -> None:
        path, value, _ = self.write_artifact("real-model-direct-performance")
        value["cases"].pop()
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "exact 12-case plan"):
            self.contract.validate_artifact(path)

        path, value, _ = self.write_artifact("real-model-direct-performance")
        value["cases"][0]["trials"][1]["repetition"] = 0
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "preserve trial order"):
            self.contract.validate_artifact(path)

    def test_summary_must_recompute_from_raw_trials(self) -> None:
        path, value, _ = self.write_artifact("long-context-prefill")
        value["cases"][0]["summary"]["prefill_tokens_per_second"] += 1
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "does not recompute"):
            self.contract.validate_artifact(path)

    def test_correctness_failure_is_not_publishable(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        case = value["cases"][0]
        case["trials"][0]["metrics"]["output_mismatch_count"] = 1
        case["summary"]["output_mismatch_count"] = 1
        value["publication"] = {
            "evidence_status": "not_publishable",
            "promotion_status": "not_evaluated",
            "candidate": False,
            "evidence_reasons": ["evidence_gate_failed:output-correctness"],
            "promotion_reasons": [],
            "supersedes": [],
        }
        self.rewrite(path, value)
        report = self.contract.validate_artifact(path)
        self.assertEqual(report["status"], "not_publishable")
        self.assertFalse(report["publication_candidate"])

    def test_promotion_failure_preserves_publishable_negative_evidence(self) -> None:
        path, value, _ = self.write_artifact(
            "online-serving-single-client",
            threshold_overrides={"ttft-bound": 0},
        )
        value["publication"] = {
            "evidence_status": "publishable",
            "promotion_status": "failed",
            "candidate": True,
            "evidence_reasons": [],
            "promotion_reasons": ["promotion_gate_failed:ttft-bound"],
            "supersedes": [],
        }
        self.rewrite(path, value)
        report = self.contract.validate_artifact(path)
        self.assertEqual(report["status"], "publishable")
        self.assertEqual(report["promotion_status"], "failed")
        self.assertTrue(report["publication_candidate"])

    def test_cli_can_require_promotion_without_changing_publication_status(self) -> None:
        path, value, _ = self.write_artifact(
            "online-serving-single-client",
            threshold_overrides={"ttft-bound": 0},
        )
        value["publication"] = {
            "evidence_status": "publishable",
            "promotion_status": "failed",
            "candidate": True,
            "evidence_reasons": [],
            "promotion_reasons": ["promotion_gate_failed:ttft-bound"],
            "supersedes": [],
        }
        self.rewrite(path, value)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "validate-performance",
                    "--contract",
                    str(CONTRACT),
                    "--evidence",
                    str(path),
                    "--require-promotion",
                ]
            )

        self.assertEqual(exit_code, 5)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["status"], "publishable")
        self.assertEqual(report["promotion_status"], "failed")

    def test_certification_record_must_match_frozen_thresholds(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        value["frozen_thresholds"]["ttft-bound"] = 123
        self.rewrite(path, value)
        with self.assertRaisesRegex(
            ContractError, "frozen thresholds do not match"
        ):
            self.contract.validate_artifact(path)

    def test_certification_record_must_apply_at_session_start(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        value["session"]["started_at"] = "2026-08-19T00:00:00Z"
        value["session"]["finished_at"] = "2026-08-19T00:01:00Z"
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "was not applicable"):
            self.contract.validate_artifact(path)

    def test_certification_record_must_match_runtime_identity(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        value["environment"]["resolved_runtime_sha256"] = "8" * 64
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "resolved_runtime_sha256.*differs"):
            self.contract.validate_artifact(path)

    def test_declared_publication_cannot_override_the_judge(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        value["environment"]["host_admission_passed"] = False
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "independently derived decision"):
            self.contract.validate_artifact(path)

    def test_host_load_admission_is_recomputed(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        value["environment"]["load_average_1m_end"] = 2.1
        value["publication"] = {
            "evidence_status": "not_publishable",
            "promotion_status": "not_evaluated",
            "candidate": False,
            "evidence_reasons": ["host_load_average_exceeded"],
            "promotion_reasons": [],
            "supersedes": [],
        }
        self.rewrite(path, value)
        report = self.contract.validate_artifact(path)
        self.assertEqual(report["status"], "not_publishable")
        self.assertFalse(report["publication_candidate"])

    def test_publication_candidate_must_be_boolean(self) -> None:
        path, value, _ = self.write_artifact("online-serving-single-client")
        value["publication"]["candidate"] = 1
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "candidate must be a boolean"):
            self.contract.validate_artifact(path)

    def test_fixture_subject_cannot_be_a_publication_candidate(self) -> None:
        path, value, _ = self.write_artifact("prefix-cache-restart")
        value["session"]["subject_kind"] = "fixture"
        value["publication"] = {
            "evidence_status": "not_publishable",
            "promotion_status": "not_evaluated",
            "candidate": False,
            "evidence_reasons": ["fixture_subject"],
            "promotion_reasons": [],
            "supersedes": [],
        }
        self.rewrite(path, value)
        report = self.contract.validate_artifact(path)
        self.assertEqual(report["status"], "not_publishable")
        self.assertFalse(report["publication_candidate"])

    def test_capability_unsupported_is_explicit_and_not_publishable(self) -> None:
        path, _, _ = self.write_artifact("accelerated-generation", unsupported=True)
        report = self.contract.validate_artifact(path)
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["case_count"], 0)
        self.assertFalse(report["publication_candidate"])

    def test_paired_claim_rejects_separate_session_semantics(self) -> None:
        path, value, _ = self.write_artifact("prefill-interference")
        value["session"]["comparison"]["measurement_semantics"] = (
            "separate_run_directional"
        )
        self.rewrite(path, value)
        with self.assertRaisesRegex(ContractError, "requires same_session"):
            self.contract.validate_artifact(path)

    def test_unknown_contract_field_fails_closed(self) -> None:
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["unexpected"] = True
        root = self.temporary_root()
        path = root / "contract.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            PerformanceContract.load(path)

    def test_v1_environment_fields_cannot_be_weakened(self) -> None:
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["environment_requirements"]["identity_fields"].remove(
            "subject_binary_sha256"
        )
        root = self.temporary_root()
        path = root / "contract.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "v1 identity contract"):
            PerformanceContract.load(path)

    def test_contract_gate_expected_value_must_be_numeric(self) -> None:
        value = json.loads(CONTRACT.read_text(encoding="utf-8"))
        value["lanes"][0]["gates"][0]["expected"] = False
        root = self.temporary_root()
        path = root / "contract.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "expected must be a number"):
            PerformanceContract.load(path)

    def test_cli_inspects_contract_and_writes_validation_report(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "inspect-performance",
                    "--contract",
                    str(CONTRACT),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["planned_case_count"], 103)

        evidence, _, _ = self.write_artifact("prefix-cache-restart")
        output = self.temporary_root() / "validation"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "validate-performance",
                    "--contract",
                    str(CONTRACT),
                    "--evidence",
                    str(evidence),
                    "--output",
                    str(output),
                ]
            )
        self.assertEqual(exit_code, 0)
        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["status"], "publishable")
        self.assertTrue((output / "report.json").is_file())
        self.assertTrue((output / "SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
