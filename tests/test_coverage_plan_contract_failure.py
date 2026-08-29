"""Canonical CoveragePlan and ContractFailure output-contract tests."""

import hashlib
import json
import unittest
from pathlib import Path

from turnvector_benchmark.authority.contract_failure import (
    CONTRACT_FAILURE_MESSAGES,
    ContractFailure,
    build_error,
)
from turnvector_benchmark.authority.coverage_plan import LIMITATIONS, canonical_object_bytes
from turnvector_benchmark.core import ContractError

from tests.fixtures.compiler.fixture_utils import build_fixture, synthetic_digest
from tests.fixtures.compiler.test_permit_issuer import payload_fields

ROOT = Path(__file__).resolve().parent.parent
PLAN_SCHEMA = ROOT / "schemas" / "coverage-plan-v1.schema.json"
FAILURE_SCHEMA = ROOT / "schemas" / "contract-failure-v1.schema.json"


class CoveragePlanContractFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()
        cls.payload = payload_fields(cls.fixture)

    def failure_fields(self):
        p = self.payload
        inputs = {
            "source_reconciliation_path": "authority/source-reconciliation-v1.json",
            "source_reconciliation_sha256": p["source_reconciliation_sha256"],
            "expectation_path": "expectations/turnvector-implementation-v2.json",
            "expectation_sha256": p["expectation_sha256"],
            "catalog_path": "authority/obligation-catalog-v1.jsonl",
            "catalog_sha256": p["catalog_sha256"],
            "traceability_path": "authority/traceability-v1.json",
            "traceability_sha256": p["traceability_sha256"],
            "authority_snapshot_sha256": p["authority_snapshot_sha256"],
            "compile_limits_sha256": p["compile_limits_sha256"],
            "input_set_sha256": p["input_set_sha256"],
        }
        observed = {name: 0 for name in (
            "authority_file_count", "authority_byte_count", "section_count",
            "section_byte_count", "serialized_input_byte_count", "catalog_record_count",
            "entity_count", "relation_record_count", "endpoint_reference_count", "path_count",
            "logical_arena_byte_count", "output_byte_count_attempted",
        )}
        return {
            "custody_domain_id": p["custody_domain_id"],
            "custody_domain_sha256": p["custody_domain_sha256"],
            "custody_lineage_id": p["custody_lineage_id"], "attempt": p["attempt"], "t_max": p["t_max"],
            "start_event_sha256": p["start_event_sha256"],
            "chronology_prefix_sha256": p["chronology_prefix_sha256"],
            "chronology_prefix_byte_count": p["chronology_prefix_byte_count"],
            "compiler_build_sha256": p["compiler_build_sha256"],
            "execution_closure_sha256": p["execution_closure_sha256"],
            "compile_custody_policy_sha256": p["compile_custody_policy_sha256"],
            "input_set_sha256": p["input_set_sha256"], "inputs": inputs, "observed": observed,
        }

    def test_output_schemas_are_strict_and_exclude_forbidden_history(self):
        for path, count in ((PLAN_SCHEMA, 23), (FAILURE_SCHEMA, 18)):
            with self.subTest(path=path.name):
                schema = json.loads(path.read_text())
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(len(schema["required"]), count)
                raw = path.read_text()
                self.assertNotIn("final_history", raw)
                self.assertNotIn("final-history", raw)
        plan = json.loads(PLAN_SCHEMA.read_text())
        source_schema = plan["$defs"]["source"]
        self.assertNotIn("strict_parser_version", source_schema["properties"])
        self.assertEqual(source_schema["properties"]["repository_control_version"]["const"],
                         "turnvector.benchmark.repository-control.v1")

    def test_contract_failure_canonical_round_trip_and_identity(self):
        failure = ContractFailure.build(
            variant="authority_invalid_identifier",
            diagnostics=["authority_invalid_identifier|/traceability_ledger/id|authority_invalid_identifier"],
            **self.failure_fields(),
        )
        self.assertEqual(failure.canonical_bytes,
                         canonical_object_bytes(json.loads(failure.canonical_bytes)))
        self.assertEqual(failure.sha256, hashlib.sha256(failure.canonical_bytes).hexdigest())
        self.assertEqual(failure.byte_count, len(failure.canonical_bytes))
        self.assertEqual(failure.error["message"],
                         CONTRACT_FAILURE_MESSAGES["authority_invalid_identifier"])
        self.assertNotIn(b"plan_sha256", failure.canonical_bytes)
        self.assertNotIn(b"issuance_kind", failure.canonical_bytes)

    def test_diagnostic_cap_keeps_first_64_and_counts_discards(self):
        variant = "traceability_relation_mismatch"
        diagnostics = [f"{variant}|/traceability_ledger/entities/gates/{i}|{variant}"
                       for i in range(65)]
        error = build_error(variant, diagnostics)
        self.assertEqual(len(error["diagnostics"]), 64)
        self.assertEqual(error["discarded_diagnostic_count"], 1)
        self.assertEqual(error["diagnostics"], tuple(diagnostics[:64]))

    def test_oversize_diagnostic_is_dropped_never_truncated(self):
        variant = "authority_unknown_field"
        oversize = f"{variant}|/" + "x" * 1100 + f"|{variant}"
        valid = f"{variant}|/traceability_ledger/unknown|{variant}"
        error = build_error(variant, [oversize, valid], 3)
        self.assertEqual(error["diagnostics"], (valid,))
        self.assertEqual(error["discarded_diagnostic_count"], 4)

    def test_diagnostic_pointer_identity_and_rfc6901_tokens(self):
        variant = "authority_unknown_field"
        diagnostic = f"{variant}|/traceability_ledger/key~0with~1slash|{variant}"
        error = build_error(variant, [diagnostic])
        self.assertEqual(error["diagnostics"][0], diagnostic)
        for malformed in (f"{variant}|relative|{variant}",
                          f"other|/traceability_ledger|{variant}",
                          f"{variant}|/traceability_ledger|other"):
            with self.subTest(value=malformed):
                with self.assertRaises(ContractError):
                    build_error(variant, [malformed])

    def test_limitations_text_and_order_are_exact(self):
        self.assertEqual(LIMITATIONS, (
            "plan proves closure relative to the accepted obligation catalog and exact source snapshot only",
            "no production behavior, adapter availability, or semantic completeness of source prose is claimed",
            "plan binds the through-START chronology prefix only; the final history digest/count is first persisted by RunEnvironment",
            "structural fixture readiness does not imply claim readiness",
        ))
        self.assertTrue(all(len(value.encode("utf-8")) <= 1024 for value in LIMITATIONS))

    def test_recursive_lexical_object_serialization_preserves_array_order(self):
        value = {"z": {"b": 2, "a": 1}, "a": [{"z": 3, "a": 4}, {"b": 5, "a": 6}]}
        raw = canonical_object_bytes(value)
        self.assertEqual(raw, b'{"a":[{"a":4,"z":3},{"a":6,"b":5}],"z":{"a":1,"b":2}}\n')


if __name__ == "__main__":
    unittest.main()
