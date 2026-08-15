from __future__ import annotations

import hashlib
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import load_suite
from turnvector_benchmark.expectation import bind_suite_lane, load_expectation
from turnvector_benchmark.runner import BenchmarkRunner


ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "suites" / "scheduler-policy-v1.json"
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v1.json"


def command_for(path: Path) -> str:
    return f"{shlex.quote(sys.executable)} -B {shlex.quote(str(path))}"


class BenchmarkRunnerTests(unittest.TestCase):
    def run_driver(self, driver: Path):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "artifact"
        expectation = load_expectation(EXPECTATION)
        suite = load_suite(SUITE)
        runner = BenchmarkRunner(
            suite=suite,
            expectation=expectation,
            lane=bind_suite_lane(expectation, "scheduler-policy", suite),
            driver_command=command_for(driver),
            driver_cwd=ROOT,
            output_dir=output,
            target_repo=None,
            response_timeout_seconds=5.0,
        )
        return runner.run()

    def test_reference_driver_passes_and_writes_verifiable_artifacts(self) -> None:
        result = self.run_driver(ROOT / "drivers" / "reference_driver.py")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.report["passed_scenario_count"], 3)

        weighted = result.report["scenarios"][0]["repetitions"][0]["metrics"]
        self.assertEqual(weighted["selection_count"], {"alpha": 12, "beta": 36})
        self.assertEqual(weighted["engine_service_share"], {"alpha": "1/4", "beta": "3/4"})
        self.assertEqual(weighted["normalized_service_spread_us"], "0/1")

        manifest = json.loads((result.artifact_dir / "manifest.json").read_text())
        self.assertEqual(manifest["implementation_expectation"]["id"], "turnvector-implementation-v1")
        self.assertEqual(manifest["lane"]["id"], "scheduler-policy")
        self.assertEqual(manifest["benchmark_scope_status"], "partial_lane")
        self.assertIn("mlx-native-correctness", manifest["unevaluated_required_lanes"])
        source_paths = {item["path"] for item in manifest["benchmark_source"]}
        self.assertIn("turnvector_benchmark/runner.py", source_paths)
        self.assertIn("schemas/scenario-v1.schema.json", source_paths)
        self.assertIn("schemas/expectation-v1.schema.json", source_paths)

        self.assertEqual(result.report["full_implementation_status"], "not_evaluated")
        self.assertEqual(result.report["required_lane_count"], 12)

        checksums = (result.artifact_dir / "SHA256SUMS").read_text(
            encoding="ascii"
        ).splitlines()
        for line in checksums:
            expected, filename = line.split("  ", 1)
            observed = hashlib.sha256(
                (result.artifact_dir / filename).read_bytes()
            ).hexdigest()
            self.assertEqual(observed, expected)

    def test_incorrect_driver_fails_conformance_with_evidence(self) -> None:
        result = self.run_driver(ROOT / "tests" / "fixtures" / "incorrect_driver.py")
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.status, "conformance_failed")
        report = json.loads((result.artifact_dir / "report.json").read_text())
        self.assertLess(report["passed_scenario_count"], report["scenario_count"])
        trace = (result.artifact_dir / "trace.jsonl").read_text(encoding="utf-8")
        self.assertIn('"status":"failed"', trace)


if __name__ == "__main__":
    unittest.main()
