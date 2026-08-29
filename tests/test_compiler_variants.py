"""Frozen 48-variant algebra and reachable helper-owner tests."""

import hashlib
import unittest

from turnvector_benchmark.authority.authority_snapshot import AuthoritySnapshot
from turnvector_benchmark.authority.bound_bytes import BoundBytesRef
from turnvector_benchmark.authority.compiler import _compile_test
from turnvector_benchmark.authority.contract_failure import ContractFailure
from turnvector_benchmark.authority.coverage_plan import CoveragePlan
from turnvector_benchmark.authority.expectation import BenchmarkExpectation
from turnvector_benchmark.authority.obligation_catalog import ObligationCatalog
from turnvector_benchmark.authority.traceability_ledger import TraceabilityLedger
from turnvector_benchmark.compile_limits import CompileLimits
from tests.fixtures.compiler.fixture_utils import build_fixture
from tests.fixtures.compiler.test_permit_issuer import issue_test_compile_permit

from turnvector_benchmark.authority.errors import (
    CONTRACT_FAILURE_MESSAGES,
    CONTRACT_FAILURE_REASON_CODES,
    CONTRACT_FAILURE_VARIANTS,
)

EXPECTED_VARIANTS = (
    "authority_invalid_canonical_json", "authority_unknown_field", "authority_duplicate_key",
    "authority_invalid_identifier", "authority_duplicate_identifier", "authority_order_violation",
    "authority_absolute_path", "authority_path_traversal", "authority_symlink",
    "authority_non_regular_file", "authority_missing_source", "authority_missing_historical_object",
    "authority_repository_mismatch", "authority_revision_mismatch", "authority_dirty_repository",
    "authority_file_digest_mismatch", "authority_invalid_section_range", "authority_section_digest_mismatch",
    "authority_repository_control_unsupported", "catalog_digest_mismatch",
    "catalog_gate_revision_mismatch", "catalog_lineage_mismatch", "catalog_predecessor_mismatch",
    "catalog_record_limit_exceeded", "catalog_orphan_source", "catalog_invalid_status",
    "expectation_digest_mismatch", "traceability_digest_mismatch", "traceability_unknown_entity",
    "traceability_duplicate_entity", "traceability_orphan_obligation", "traceability_orphan_case",
    "traceability_orphan_judge", "traceability_orphan_bundle", "traceability_orphan_gate",
    "traceability_orphan_evidence", "traceability_relation_mismatch",
    "traceability_degree_cap_exceeded", "traceability_empty_required_set",
    "traceability_negative_test_coverage_missing", "resource_checked_arithmetic_overflow",
    "resource_platform_size_overflow", "resource_source_cap_exceeded",
    "resource_section_cap_exceeded", "resource_input_cap_exceeded",
    "resource_index_cap_exceeded", "resource_output_cap_exceeded", "resource_allocation_failed",
)


class CompilerVariantTests(unittest.TestCase):
    @staticmethod
    def compile_fixture(fixture):
        bound = BoundBytesRef
        return _compile_test(
            issue_test_compile_permit(fixture),
            AuthoritySnapshot(bound(fixture.snapshot_bytes), bound(fixture.reconciliation_bytes)),
            ObligationCatalog(bound(fixture.catalog_bytes)),
            BenchmarkExpectation(bound(fixture.expectation_bytes)),
            TraceabilityLedger(bound(fixture.ledger_bytes)),
            CompileLimits.frozen(),
        )

    def test_complete_base_fixture_compiles_to_coverage_plan(self):
        result = self.compile_fixture(build_fixture())
        self.assertIs(type(result), CoveragePlan)
        self.assertEqual(result.to_dict()["counts"]["enforcement_path_count"], 2287)
        self.assertEqual(result.to_dict()["counts"]["path_evidence_record_count"], 10693)
        self.assertEqual(result.canonical_bytes[-1:], b"\n")

    def test_stage_one_failure_materializes_contract_failure(self):
        fixture = build_fixture()
        fixture.snapshot_bytes = b"{\n"
        result = self.compile_fixture(fixture)
        self.assertIs(type(result), ContractFailure)
        self.assertEqual(result.error["variant"], "authority_invalid_canonical_json")

    def test_exact_48_name_ordinal_is_frozen(self):
        self.assertEqual(CONTRACT_FAILURE_VARIANTS, EXPECTED_VARIANTS)
        self.assertEqual(len(CONTRACT_FAILURE_VARIANTS), 48)
        self.assertEqual(len(set(CONTRACT_FAILURE_VARIANTS)), 48)

    def test_every_variant_has_its_exact_reason_code(self):
        self.assertEqual(tuple(CONTRACT_FAILURE_REASON_CODES[v] for v in EXPECTED_VARIANTS),
                         EXPECTED_VARIANTS)

    def test_all_exact_variant_message_pairs_match_frozen_golden(self):
        raw = "\n".join(f"{variant}|{CONTRACT_FAILURE_MESSAGES[variant]}"
                        for variant in EXPECTED_VARIANTS).encode("ascii") + b"\n"
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "443655f551cc9ddd88b26a1b24551a774a44f29cad4dae519feccbbb0dd12eef")
        self.assertTrue(all(message and message.isascii()
                            for message in CONTRACT_FAILURE_MESSAGES.values()))

    def test_historical_object_message_uses_v14_multiset_wording(self):
        message = CONTRACT_FAILURE_MESSAGES["authority_missing_historical_object"]
        self.assertEqual(message,
                         "historical object set disagrees with the reconciliation mappings")
        self.assertNotEqual(message, "reconciliation mapping has no historical object record")

    def test_resource_variants_occupy_ordinals_41_through_48(self):
        self.assertEqual(EXPECTED_VARIANTS[40:], (
            "resource_checked_arithmetic_overflow", "resource_platform_size_overflow",
            "resource_source_cap_exceeded", "resource_section_cap_exceeded",
            "resource_input_cap_exceeded", "resource_index_cap_exceeded",
            "resource_output_cap_exceeded", "resource_allocation_failed",
        ))

    def test_semantic_precedence_is_strictly_before_resource_algebra(self):
        for semantic in EXPECTED_VARIANTS[:40]:
            for resource in EXPECTED_VARIANTS[40:]:
                self.assertLess(EXPECTED_VARIANTS.index(semantic),
                                EXPECTED_VARIANTS.index(resource))


if __name__ == "__main__":
    unittest.main()
