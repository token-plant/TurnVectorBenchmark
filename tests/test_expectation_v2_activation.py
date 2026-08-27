from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

from turnvector_benchmark.controller import LaneController
from turnvector_benchmark.expectation import load_expectation
from turnvector_benchmark.lane_runner import LaneContext, LaneResult
from turnvector_benchmark.owner_lifecycle_fixture import OWNER_LIFECYCLE_FIXTURE_ID
from turnvector_benchmark.subject import SubjectHello, SubjectIdentity


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v2.json"
SUBJECT = ROOT / "subjects" / "reference-fixture-v1.json"
CERTIFICATION = ROOT / "certification" / "reference-fixture-v1.json"
PAIRED_TURNVECTOR = Path("/Users/chenyu/.codex/worktrees/da20/TurnVector")


class _PassingRunnerStub:
    """Dispatch stub that records provenance and passes every case."""

    lane_id = "protocol-and-owner-lifecycle"

    def __init__(self) -> None:
        self.bound_provenance: Optional[tuple] = None

    def run(
        self, context: LaneContext, subject: Any, hello: Any
    ) -> LaneResult:
        del subject, hello
        self.bound_provenance = (
            context.execution_provenance,
            context.fixture_id,
        )
        return LaneResult(
            lane_id=context.lane.lane_id,
            status="passed",
            case_count=len(context.plan.cases),
            executed_case_count=len(context.plan.cases),
            metrics={},
            gates=(),
            failures=(),
            artifacts=(),
            raw_records=(),
        )


class _FakeImplementationSession:
    """SubjectAdapter v1 session double that presents an implementation subject."""

    def __init__(self, adapter: Any) -> None:
        del adapter
        self.transcript: list = []
        driver = ROOT / "drivers" / "reference_subject.py"
        self._binary = (
            {
                "path": str(driver),
                "sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            },
        )

    def __enter__(self) -> "_FakeImplementationSession":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def hello(self, run_id: str, lane_id: str, lane_protocol: str) -> SubjectHello:
        del run_id
        return SubjectHello(
            identity=SubjectIdentity(
                name="implementation-test-subject",
                version="1",
                kind="implementation",
                build_identity="reference-fixture-build-v1",
            ),
            supported_lanes={lane_id: lane_protocol},
            binary_manifest=self._binary,
            dependency_manifest=(),
            environment_identity={
                "device_class": "fixture",
                "memory_bytes": 1048576,
                "os_build": "fixture",
            },
            data_plane=None,
        )

    def finish(self) -> None:
        pass

    def stderr_text(self) -> str:
        return ""


class ExpectationV2ActivationTests(unittest.TestCase):
    def output_path(self, name: str = "artifact") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / name

    def implementation_subject(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        value = json.loads(SUBJECT.read_text(encoding="utf-8"))
        value["subject_kind"] = "implementation"
        for adapter in value["adapters"]:
            adapter["cwd"] = str(ROOT)
        path = Path(temporary.name) / "implementation-subject.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def daemon_build_fixtures(self) -> Path:
        """A hash-verified external fixture manifest binding a daemon-build input."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        daemon = root / "turnvector-daemon.bin"
        daemon.write_bytes(b"successor-daemon-build")
        lock = ROOT / "oracles" / "mlx" / "reference-lock-v1.json"
        value = {
            "schema_version": "turnvector.benchmark.external-fixtures.v1",
            "id": "daemon-build-fixtures",
            "reference_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "artifacts": [
                {
                    "id": "daemon-build",
                    "path": str(daemon),
                    "kind": "file",
                    "size": daemon.stat().st_size,
                    "sha256": hashlib.sha256(daemon.read_bytes()).hexdigest(),
                }
            ],
        }
        path = root / "external-fixtures.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def controller(self, output: Path) -> LaneController:
        return LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=self.implementation_subject(),
            certification_record_path=CERTIFICATION,
            external_fixture_manifest_path=self.daemon_build_fixtures(),
            output_dir=output,
            target_repo=None,
        )

    def test_v2_is_the_only_active_expectation(self) -> None:
        self.assertFalse(
            (ROOT / "expectations" / "turnvector-implementation-v1.json").exists()
        )
        self.assertTrue(EXPECTATION.is_file())
        expectation = load_expectation(EXPECTATION)
        self.assertEqual(expectation.expectation_id, "turnvector-implementation-v2")
        self.assertEqual(
            expectation.schema_version, "turnvector.benchmark.expectation.v3"
        )
        self.assertEqual(len(expectation.lanes), 12)
        summary = LaneController.inspect(expectation_path=EXPECTATION, target_repo=None)
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["lane_suite_count"], 12)
        self.assertEqual(summary["qualification_case_count"], 425)
        self.assertEqual(summary["self_test_gate_count"], 58)

    def test_reference_fixture_full_run_is_425_cases_58_gates_and_tainted(self) -> None:
        output = self.output_path()
        controller = LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=SUBJECT,
            certification_record_path=CERTIFICATION,
            external_fixture_manifest_path=None,
            output_dir=output,
            target_repo=None,
        )
        result = controller.run_all()
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.report["passed_lane_count"], 12)
        self.assertEqual(result.report["qualification_case_count"], 425)
        self.assertEqual(result.report["executed_case_count"], 425)
        self.assertEqual(
            result.report["lane_status_counts"], {"passed": 12}
        )
        # The owner-lifecycle lane is fixture-selected in the active successor
        # contract, so the reference fixture run is fixture_tainted and never
        # claimable.
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertFalse(result.report["claimable"])
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])

    def test_owner_fixture_taints_a_passing_non_fixture_subject(self) -> None:
        """Activation: a non-fixture subject with otherwise passing results.

        The owner-lifecycle lane is fixture-selected through the active seam, so
        the run is fixture_tainted, not_claimable_fixture, and claimable false
        even though the subject is an implementation with passing results.
        """
        output = self.output_path()
        controller = self.controller(output)
        stub = _PassingRunnerStub()
        with patch.dict(
            "turnvector_benchmark.controller.LANE_RUNNER_REGISTRY",
            {"protocol-and-owner-lifecycle": stub},
        ):
            with patch(
                "turnvector_benchmark.controller.SubjectSession",
                _FakeImplementationSession,
            ):
                result = controller.run_lane("protocol-and-owner-lifecycle")
        self.assertEqual(result.report["lanes"][0]["status"], "passed")
        self.assertEqual(result.report["lanes"][0]["executed_case_count"], 24)
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        self.assertFalse(result.report["claimable"])
        self.assertEqual(
            stub.bound_provenance,
            ("benchmark_fixture", OWNER_LIFECYCLE_FIXTURE_ID),
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])

    @unittest.skipUnless(
        PAIRED_TURNVECTOR.is_dir(),
        "paired TurnVector checkout is not available on this runner",
    )
    def test_paired_inspect_reports_exact_source_match_at_eedad5f(self) -> None:
        report = LaneController.inspect(
            expectation_path=EXPECTATION,
            target_repo=PAIRED_TURNVECTOR,
        )
        source_contract = report["source_contract"]
        self.assertTrue(source_contract["matches"])
        self.assertEqual(source_contract["status"], "matched")
        self.assertEqual(
            source_contract["observed_revision"],
            "eedad5faf881da329844463eeaf54d9970350abd",
        )
        self.assertIn(source_contract["revision_relation"], {"exact", "descendant"})
        self.assertFalse(source_contract["dirty"])
        self.assertEqual(report["qualification_case_count"], 425)
        self.assertEqual(report["self_test_gate_count"], 58)
        self.assertEqual(report["lane_suite_count"], 12)


if __name__ == "__main__":
    unittest.main()
