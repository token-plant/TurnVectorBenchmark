from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError, load_suite
from turnvector_benchmark.expectation import (
    bind_suite_lane,
    expectation_summary,
    load_expectation,
)


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "expectations" / "turnvector-implementation-v2.json"
SUITE = ROOT / "suites" / "scheduler-policy-v1.json"


class ImplementationExpectationTests(unittest.TestCase):
    def test_checked_in_expectation_has_all_executable_judges(self) -> None:
        expectation = load_expectation(MANIFEST)
        summary = expectation_summary(expectation, None)

        self.assertEqual(summary["required_lane_count"], 12)
        self.assertEqual(summary["executable_required_lane_count"], 12)
        self.assertEqual(summary["contract_only_required_lane_count"], 0)
        self.assertTrue(summary["full_run_available"])
        self.assertEqual(summary["claim_status"], "not_evaluated")
        self.assertEqual(summary["unexecutable_required_lane_ids"], [])
        self.assertEqual(summary["expanded_matrix_case_count"], 425)

    def test_v2_expectation_binds_v3_schema_and_hash_bound_authority(self) -> None:
        expectation = load_expectation(MANIFEST)
        self.assertEqual(expectation.schema_version, "turnvector.benchmark.expectation.v3")
        self.assertEqual(expectation.expectation_id, "turnvector-implementation-v2")
        self.assertEqual(
            expectation.source_contract.revision,
            "eedad5faf881da329844463eeaf54d9970350abd",
        )
        self.assertTrue(expectation.source_contract.clean_required)
        self.assertIsNotNone(expectation.authority)
        assert expectation.authority is not None
        self.assertEqual(
            expectation.authority.source_reconciliation_path,
            "authority/source-reconciliation-v1.json",
        )
        self.assertEqual(
            expectation.authority.source_reconciliation_sha256,
            "13706cdd46416d394fe3b8c9ce47203c7b77367199be31f502eb3b7122db7a1c",
        )
        self.assertEqual(
            expectation.lane("protocol-and-owner-lifecycle").claim_scope,
            ("local_client_protocol_conformance", "device_owner_lifecycle_conformance"),
        )
        self.assertEqual(
            expectation.lane("protocol-and-owner-lifecycle").layer,
            "daemon-and-device-owner-lifecycle",
        )
        self.assertEqual(
            expectation.lane("protocol-and-owner-lifecycle").harness.protocol,
            "turnvector.benchmark.owner-lifecycle.v1",
        )
        self.assertEqual(
            expectation.lane("bounded-turn-and-ffi").layer,
            "in-process-native-boundary",
        )
        self.assertEqual(
            expectation.lane("mlx-native-correctness").layer,
            "in-process-native-runtime",
        )
        self.assertEqual(
            expectation.lane("mlx-native-correctness").cases[0].requirement,
            "MLX model, graph, stream, KV state, execution, and teardown remain on the "
            "Device Executor lifecycle owner thread.",
        )
        certification = expectation.lane("certification-envelopes")
        matrices = {
            matrix.matrix_id: {
                dimension.dimension_id: dimension.values
                for dimension in matrix.dimensions
            }
            for matrix in certification.matrices
        }
        self.assertEqual(len(matrices["applicability-drift"]["changed_identity"]), 19)
        self.assertEqual(
            matrices["applicability-drift"]["record_state"],
            ("applicable", "missing", "stale_evidence", "quarantined", "superseded"),
        )

    def _staged_expectation(self, value: dict) -> Path:
        """Stage a modified expectation with the authority file beside it.

        The v3 loader resolves ``authority/source-reconciliation-v1.json``
        against the benchmark repository root that contains ``expectations/``
        (the expectation's ``parent.parent``), so a temp expectation must live
        at ``<temp>/expectations/expectation.json`` and the authority file at
        ``<temp>/authority/source-reconciliation-v1.json``.
        """
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        authority_dir = root / "authority"
        authority_dir.mkdir()
        authority_dir.joinpath("source-reconciliation-v1.json").write_bytes(
            (ROOT / "authority" / "source-reconciliation-v1.json").read_bytes()
        )
        expectations_dir = root / "expectations"
        expectations_dir.mkdir()
        path = expectations_dir / "expectation.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_v2_authority_path_drift_fails_closed(self) -> None:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        value["authority"]["source_reconciliation_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "does not match"):
            load_expectation(self._staged_expectation(value))

    def test_v2_authority_symlink_escape_fails_closed(self) -> None:
        # A repository-contained symlink component that resolves outside the
        # repository root is an authority-path escape: the containment check
        # must reject it before any read or digest comparison, so even a
        # byte-identical authority payload behind the symlink cannot be used.
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        value["authority"]["source_reconciliation_path"] = (
            "authority-link/source-reconciliation-v1.json"
        )
        temporary = tempfile.TemporaryDirectory()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(outside.cleanup)
        root = Path(temporary.name)
        outside_dir = Path(outside.name)
        outside_dir.joinpath("source-reconciliation-v1.json").write_bytes(
            (ROOT / "authority" / "source-reconciliation-v1.json").read_bytes()
        )
        (root / "authority-link").symlink_to(outside_dir, target_is_directory=True)
        expectations_dir = root / "expectations"
        expectations_dir.mkdir()
        path = expectations_dir / "expectation.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "escapes the repository root"):
            load_expectation(path)

    def test_v2_authority_requires_v3_schema_binding(self) -> None:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        value["schema_version"] = "turnvector.benchmark.expectation.v2"
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            load_expectation(self._staged_expectation(value))

    def test_mlx_expectation_fixes_native_matrix_and_parity_gates(self) -> None:
        expectation = load_expectation(MANIFEST)
        lane = expectation.lane("mlx-native-correctness")
        matrices = {
            matrix.matrix_id: {
                dimension.dimension_id: dimension.values
                for dimension in matrix.dimensions
            }
            for matrix in lane.matrices
        }
        gates = {gate.gate_id: gate for gate in lane.gates}

        self.assertEqual(matrices["decode"]["model_architecture"], ("dense", "moe"))
        self.assertEqual(matrices["decode"]["batch_size"], (1, 4))
        self.assertEqual(matrices["decode"]["context_tokens"], (512, 2048, 8192))
        self.assertEqual(matrices["prefill"]["prompt_tokens"], (64, 256, 1024))
        self.assertEqual(gates["output-parity"].expected, 0)
        self.assertEqual(gates["logits-parity"].expected, 0)
        self.assertEqual(gates["kv-parity"].expected, 0)
        self.assertEqual(lane.harness.status, "executable")
        self.assertTrue(lane.required)

    def test_scheduler_suite_is_bound_to_exact_executable_lane(self) -> None:
        expectation = load_expectation(MANIFEST)
        suite = load_suite(SUITE)
        lane = bind_suite_lane(expectation, "scheduler-policy", suite)
        self.assertEqual(lane.harness.status, "executable")

        with self.assertRaisesRegex(ContractError, "no legacy JSONL suite"):
            bind_suite_lane(expectation, "mlx-native-correctness", suite)

    def test_unknown_expectation_field_fails_closed(self) -> None:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        value["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expectation.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unknown fields"):
                load_expectation(path)


if __name__ == "__main__":
    unittest.main()
