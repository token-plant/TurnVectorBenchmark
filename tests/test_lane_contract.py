from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.external import load_external_fixture_manifest
from turnvector_benchmark.expectation import load_expectation
from turnvector_benchmark.lane_contract import (
    expand_case_plan,
    load_all_lane_suites,
    load_certification_record,
    load_lane_suite,
    validate_certification_contract,
    validate_certification_identity,
)


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v1.json"
CERTIFICATION = ROOT / "certification" / "reference-fixture-v1.json"
REFERENCE_LOCK = ROOT / "oracles" / "mlx" / "reference-lock-v1.json"


class LaneContractTests(unittest.TestCase):
    def test_suite_cannot_remove_an_expectation_matrix(self) -> None:
        expectation = load_expectation(EXPECTATION)
        lane = expectation.lane("mlx-native-correctness")
        source = ROOT / "suites" / "lanes" / "mlx-native-correctness-v1.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value["matrix_ids"] = ["decode"]
        value["case_schema"] = str(
            ROOT / "schemas" / "lanes" / "mlx-native-correctness-case-v1.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "must exactly match expectation order"):
                load_lane_suite(path, lane)

    def test_case_schema_cannot_remove_a_context_value(self) -> None:
        expectation = load_expectation(EXPECTATION)
        lane = expectation.lane("mlx-native-correctness")
        suite_source = ROOT / "suites" / "lanes" / "mlx-native-correctness-v1.json"
        schema_source = ROOT / "schemas" / "lanes" / "mlx-native-correctness-case-v1.json"
        suite = json.loads(suite_source.read_text(encoding="utf-8"))
        schema = json.loads(schema_source.read_text(encoding="utf-8"))
        schema["matrices"]["decode"]["properties"]["context_tokens"]["enum"] = [512, 2048]
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            schema_path = directory_path / "case-schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            suite["case_schema"] = str(schema_path)
            suite_path = directory_path / "suite.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "must exactly match expectation values"):
                load_lane_suite(suite_path, lane)

    def test_external_fixture_hash_precheck_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "fixture.bin"
            artifact.write_bytes(b"fixed-before-run")
            manifest = {
                "schema_version": "turnvector.benchmark.external-fixtures.v1",
                "id": "test-fixtures",
                "reference_lock_sha256": hashlib.sha256(
                    REFERENCE_LOCK.read_bytes()
                ).hexdigest(),
                "artifacts": [
                    {
                        "id": "dense-model",
                        "path": str(artifact),
                        "kind": "file",
                        "size": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                ],
            }
            manifest_path = root / "external.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = load_external_fixture_manifest(
                manifest_path, reference_lock=REFERENCE_LOCK
            )
            self.assertEqual(loaded.artifacts["dense-model"].size, 16)

            artifact.write_bytes(b"changed-after-freeze")
            with self.assertRaisesRegex(ContractError, "identity mismatch"):
                load_external_fixture_manifest(manifest_path, reference_lock=REFERENCE_LOCK)

    def test_certification_record_build_and_environment_are_exact(self) -> None:
        expectation = load_expectation(EXPECTATION)
        suites = load_all_lane_suites(expectation)
        lane = expectation.lane("scheduler-performance")
        plan = expand_case_plan(lane, suites[lane.lane_id])
        record = load_certification_record(CERTIFICATION)
        validate_certification_contract(record, expectation)
        with self.assertRaisesRegex(ContractError, "does not apply to subject build"):
            validate_certification_identity(
                record,
                subject_build_identity="different-build",
                environment_identity={
                    "device_class": "fixture",
                    "memory_bytes": 1048576,
                    "os_build": "fixture",
                },
                plan=plan,
            )
        with self.assertRaisesRegex(ContractError, "environment identity"):
            validate_certification_identity(
                record,
                subject_build_identity="reference-fixture-build-v1",
                environment_identity={
                    "device_class": "fixture",
                    "memory_bytes": 1,
                    "os_build": "fixture",
                },
                plan=plan,
            )

        with self.assertRaisesRegex(ContractError, "environment identity"):
            validate_certification_identity(
                record,
                subject_build_identity="reference-fixture-build-v1",
                environment_identity={
                    "device_class": "fixture",
                    "memory_bytes": 1048576,
                    "os_build": "fixture",
                    "unbound_extra": True,
                },
                plan=plan,
            )

    def test_certification_record_must_cover_every_matrix_dimension(self) -> None:
        expectation = load_expectation(EXPECTATION)
        value = json.loads(CERTIFICATION.read_text(encoding="utf-8"))
        del value["matrix_applicability"]["service_class"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            record = load_certification_record(path)
            with self.assertRaisesRegex(ContractError, "every qualification dimension"):
                validate_certification_contract(record, expectation)


if __name__ == "__main__":
    unittest.main()
