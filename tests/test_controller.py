from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

from turnvector_benchmark.controller import LaneController, _status_for_results
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.expectation import load_expectation
from turnvector_benchmark.lane_contract import resolve_gate_threshold
from turnvector_benchmark.lane_runner import LaneResult


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v1.json"
SUBJECT = ROOT / "subjects" / "reference-fixture-v1.json"
CERTIFICATION = ROOT / "certification" / "reference-fixture-v1.json"


class LaneControllerTests(unittest.TestCase):
    def output_path(self, name: str = "artifact") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / name

    def controller(
        self,
        output: Path,
        *,
        subject: Optional[Path] = SUBJECT,
        certification: Optional[Path] = CERTIFICATION,
    ) -> LaneController:
        return LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=subject,
            certification_record_path=certification,
            external_fixture_manifest_path=None,
            output_dir=output,
            target_repo=None,
        )

    def modified_subject(
        self,
        lane_id: str,
        *extra_arguments: str,
        timeout_seconds: Optional[float] = None,
    ) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        value = json.loads(SUBJECT.read_text(encoding="utf-8"))
        for adapter in value["adapters"]:
            adapter["cwd"] = str(ROOT)
            if lane_id in adapter["lanes"]:
                adapter["command"].extend(extra_arguments)
                if timeout_seconds is not None:
                    adapter["timeout_seconds"] = timeout_seconds
        path = Path(temporary.name) / "subject.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_inspect_derives_complete_385_case_readiness(self) -> None:
        report = LaneController.inspect(
            expectation_path=EXPECTATION,
            target_repo=None,
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["lane_suite_count"], 12)
        self.assertEqual(report["registered_lane_runner_count"], 12)
        self.assertEqual(report["evidence_oracle_count"], 12)
        self.assertTrue(report["oracle_registry_complete"])
        self.assertEqual(report["self_test_gate_count"], 58)
        self.assertEqual(report["qualification_case_count"], 385)
        self.assertEqual(report["readiness"], "derived_complete")

    def test_reference_fixture_runs_all_cases_but_is_never_claimable(self) -> None:
        output = self.output_path()
        result = self.controller(output).run_all()

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.report["passed_lane_count"], 12)
        self.assertEqual(result.report["qualification_case_count"], 385)
        self.assertEqual(result.report["executed_case_count"], 385)
        self.assertEqual(result.report["full_implementation_status"], "not_claimable_fixture")
        self.assertFalse(result.report["claimable"])
        self.assertEqual(result.report["observed_lane_statuses"], ["passed"])
        self.assertEqual(result.report["lane_status_counts"], {"passed": 12})
        for lane in result.report["lanes"]:
            lane_dir = output / "lanes" / lane["lane_id"]
            for name in (
                "case-plan.json",
                "manifest.json",
                "environment.json",
                "metrics.json",
                "gates.json",
                "failures.json",
                "report.json",
                "SHA256SUMS",
            ):
                self.assertTrue((lane_dir / name).is_file(), f"missing {lane['lane_id']}/{name}")
        self._verify_checksums(output)

    def test_missing_adapter_is_unsupported_and_required_failure(self) -> None:
        output = self.output_path()
        result = self.controller(output, subject=None).run_all()
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.report["passed_lane_count"], 0)
        self.assertEqual(result.report["full_implementation_status"], "failed")
        self.assertTrue(all(item["status"] == "unsupported" for item in result.report["lanes"]))

    def test_missing_certification_record_fails_before_performance_execution(self) -> None:
        output = self.output_path()
        result = self.controller(output, certification=None).run_lane("scheduler-performance")
        self.assertEqual(result.status, "contract_failed")
        lane = result.report["lanes"][0]
        self.assertEqual(lane["executed_case_count"], 0)
        self.assertIn("certification record", lane["failures"][0]["message"])

    def test_incomplete_certification_record_fails_before_subject_execution(self) -> None:
        value = json.loads(CERTIFICATION.read_text(encoding="utf-8"))
        del value["thresholds"]["scheduler-performance"]["decisions_per_second"]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        record = Path(temporary.name) / "certification.json"
        record.write_text(json.dumps(value), encoding="utf-8")
        output = self.output_path()
        result = self.controller(output, certification=record).run_lane(
            "scheduler-performance"
        )
        self.assertEqual(result.status, "contract_failed")
        lane = result.report["lanes"][0]
        self.assertEqual(lane["executed_case_count"], 0)
        transcript = (
            output
            / "lanes"
            / "scheduler-performance"
            / "subject-transcript.jsonl"
        )
        self.assertFalse(transcript.exists())
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("must exactly match", manifest["certification_contract_failure"])

    def test_threshold_snapshot_accumulates_failures_and_blocks_execution(self) -> None:
        controller = self.controller(self.output_path())

        def fail_selected_thresholds(
            lane_id: str,
            gate: Any,
            record: Any,
            *,
            observed_at: Any = None,
        ) -> Any:
            if gate.gate_id in {"decision-latency", "decision-throughput"}:
                raise ContractError(f"cannot freeze {gate.gate_id}")
            return resolve_gate_threshold(
                lane_id, gate, record, observed_at=observed_at
            )

        with patch(
            "turnvector_benchmark.controller.resolve_gate_threshold",
            side_effect=fail_selected_thresholds,
        ):
            result = controller.run_lane("scheduler-performance")

        self.assertEqual(result.status, "contract_failed")
        self.assertEqual(result.report["lanes"][0]["executed_case_count"], 0)
        failure_message = result.report["lanes"][0]["failures"][0]["message"]
        self.assertIn("decision-latency", failure_message)
        self.assertIn("decision-throughput", failure_message)
        manifest = json.loads(
            (result.artifact_dir / "manifest.json").read_text(encoding="utf-8")
        )
        snapshot = manifest["pre_run_thresholds"]
        self.assertFalse(snapshot["complete"])
        self.assertFalse(snapshot["frozen"])
        self.assertEqual(len(snapshot["failures"]["scheduler-performance"]), 2)
        transcript = (
            result.artifact_dir
            / "lanes"
            / "scheduler-performance"
            / "subject-transcript.jsonl"
        )
        self.assertFalse(transcript.exists())

    def test_gate_evaluation_uses_thresholds_frozen_before_execution(self) -> None:
        controller = self.controller(self.output_path())
        assert controller.certification_record is not None
        record_type = type(controller.certification_record)
        with patch.object(
            record_type,
            "is_expired",
            side_effect=(False, False, True),
        ) as expiry_check:
            result = controller.run_lane("scheduler-performance")

        self.assertEqual(result.status, "passed")
        self.assertEqual(expiry_check.call_count, 2)

    def test_path_escape_and_missing_artifact_fail_closed(self) -> None:
        escaped = self.modified_subject("core-event-replay", "--escape-artifact")
        escape_result = self.controller(
            self.output_path("escape"), subject=escaped
        ).run_lane("core-event-replay")
        self.assertEqual(escape_result.status, "contract_failed")
        self.assertIn(
            "escapes artifact root",
            escape_result.report["lanes"][0]["failures"][0]["message"],
        )

        missing = self.modified_subject(
            "core-event-replay", "--omit-artifact", "event_trace"
        )
        missing_result = self.controller(
            self.output_path("missing"), subject=missing
        ).run_lane("core-event-replay")
        self.assertEqual(missing_result.status, "contract_failed")
        self.assertIn(
            "missing required raw artifacts",
            missing_result.report["lanes"][0]["failures"][0]["message"],
        )

    def test_malformed_protocol_and_timeout_are_preserved(self) -> None:
        malformed = self.modified_subject("core-event-replay", "--malformed-hello")
        malformed_result = self.controller(
            self.output_path("malformed"), subject=malformed
        ).run_lane("core-event-replay")
        self.assertEqual(malformed_result.status, "contract_failed")
        self.assertIn(
            "unknown fields",
            malformed_result.report["lanes"][0]["failures"][0]["message"],
        )

        timeout = self.modified_subject(
            "core-event-replay",
            "--timeout-kind",
            "hello",
            timeout_seconds=0.05,
        )
        timeout_result = self.controller(
            self.output_path("timeout"), subject=timeout
        ).run_lane("core-event-replay")
        self.assertEqual(timeout_result.status, "contract_failed")
        self.assertIn(
            "timed out",
            timeout_result.report["lanes"][0]["failures"][0]["message"],
        )

    def test_every_gate_rejects_its_incorrect_fixture(self) -> None:
        expectation = load_expectation(EXPECTATION)
        for lane in expectation.lanes:
            for gate in lane.gates:
                full_gate_id = f"{lane.lane_id}.{gate.gate_id}"
                with self.subTest(gate=full_gate_id):
                    subject = self.modified_subject(
                        lane.lane_id, "--fail-gate", full_gate_id
                    )
                    result = self.controller(
                        self.output_path(full_gate_id), subject=subject
                    ).run_lane(lane.lane_id)
                    self.assertEqual(result.status, "gate_failed")
                    gate_results = {
                        item["gate_id"]: item
                        for item in result.report["lanes"][0]["gates"]
                    }
                    self.assertEqual(gate_results[gate.gate_id]["status"], "failed")

    def test_run_all_continues_after_one_lane_gate_failure(self) -> None:
        subject = self.modified_subject(
            "core-event-replay",
            "--fail-gate",
            "core-event-replay.replay-hash",
        )
        result = self.controller(self.output_path(), subject=subject).run_all()
        self.assertEqual(result.status, "gate_failed")
        self.assertEqual(result.report["passed_lane_count"], 11)
        by_lane: Dict[str, Dict[str, Any]] = {
            item["lane_id"]: item for item in result.report["lanes"]
        }
        self.assertEqual(by_lane["core-event-replay"]["status"], "gate_failed")
        self.assertEqual(by_lane["certification-envelopes"]["status"], "passed")
        self.assertEqual(by_lane["certification-envelopes"]["executed_case_count"], 55)

    def test_gate_failure_is_not_hidden_by_unsupported_coverage(self) -> None:
        def result(status: str) -> LaneResult:
            return LaneResult(
                lane_id=status,
                status=status,
                case_count=1,
                executed_case_count=0,
                metrics={},
                gates=(),
                failures=(),
                artifacts=(),
                raw_records=(),
            )

        self.assertEqual(
            _status_for_results([result("unsupported"), result("gate_failed")]),
            "gate_failed",
        )

    def _verify_checksums(self, root: Path) -> None:
        for line in (root / "SHA256SUMS").read_text(encoding="ascii").splitlines():
            expected, relative = line.split("  ", 1)
            observed = hashlib.sha256((root / relative).read_bytes()).hexdigest()
            self.assertEqual(observed, expected, relative)


if __name__ == "__main__":
    unittest.main()
