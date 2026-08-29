"""Focused tests for the CoverageCompiler T1 boundary contracts."""

import unittest

from turnvector_benchmark.authority.bound_bytes import BoundBytesRef
from turnvector_benchmark.authority.errors import (
    CONTRACT_FAILURE_MESSAGES,
    CONTRACT_FAILURE_REASON_CODES,
    CONTRACT_FAILURE_VARIANTS,
    CompilerInternalError,
    CompilerPreconditionViolation,
)


EXPECTED_VARIANTS_AND_MESSAGES = (
    ("authority_invalid_canonical_json", "authority input is not strict canonical JSON"),
    ("authority_unknown_field", "authority input contains an unknown, missing, or mistyped field"),
    ("authority_duplicate_key", "authority input contains a duplicate object key"),
    ("authority_invalid_identifier", "authority input contains an invalid identifier, enum, or typed scalar"),
    ("authority_duplicate_identifier", "authority input declares a duplicate scalar identifier"),
    ("authority_order_violation", "authority input violates canonical order or byte re-encoding"),
    ("authority_absolute_path", "authority input contains an absolute path"),
    ("authority_path_traversal", "authority input contains a path traversal"),
    ("authority_symlink", "authority snapshot claims a symlink"),
    ("authority_non_regular_file", "authority snapshot claims a non-regular file"),
    ("authority_missing_source", "catalog source has no snapshot file record"),
    ("authority_missing_historical_object", "historical object set disagrees with the reconciliation mappings"),
    ("authority_repository_mismatch", "repository identity or clean-required status disagrees across authority inputs"),
    ("authority_revision_mismatch", "revision identity disagrees within or across authority inputs"),
    ("authority_dirty_repository", "required clean repository is dirty"),
    ("authority_file_digest_mismatch", "authority file or historical-object digest disagrees with the catalog or reconciliation"),
    ("authority_invalid_section_range", "section range is invalid or the section set mismatches"),
    ("authority_section_digest_mismatch", "section digest disagrees with the catalog"),
    ("authority_repository_control_unsupported", "repository-control descriptor is unsupported"),
    ("catalog_digest_mismatch", "ledger catalog digest disagrees with the catalog input"),
    ("catalog_gate_revision_mismatch", "catalog design-gate revision disagrees with the header or ledger"),
    ("catalog_lineage_mismatch", "custody lineage binding disagrees with the permit"),
    ("catalog_predecessor_mismatch", "catalog predecessor disagrees with the ledger or the current lineage"),
    ("catalog_record_limit_exceeded", "catalog record count exceeds the limit or mismatches"),
    ("catalog_orphan_source", "snapshot source file is not cited by any obligation"),
    ("catalog_invalid_status", "catalog readiness and blocker algebra is invalid"),
    ("expectation_digest_mismatch", "expectation digest disagrees with the catalog or ledger"),
    ("traceability_digest_mismatch", "traceability bind digest disagrees with an input"),
    ("traceability_unknown_entity", "traceability references an unknown entity"),
    ("traceability_duplicate_entity", "traceability contains a duplicate entity or relation record"),
    ("traceability_orphan_obligation", "required obligation is orphaned"),
    ("traceability_orphan_case", "expectation behavior case is orphaned"),
    ("traceability_orphan_judge", "judge is orphaned"),
    ("traceability_orphan_bundle", "evidence bundle is orphaned"),
    ("traceability_orphan_gate", "gate is orphaned"),
    ("traceability_orphan_evidence", "evidence source is orphaned"),
    ("traceability_relation_mismatch", "traceability relation or count mismatches"),
    ("traceability_degree_cap_exceeded", "traceability degree cap is exceeded"),
    ("traceability_empty_required_set", "a required traceability set is empty"),
    ("traceability_negative_test_coverage_missing", "negative-test coverage is missing"),
    ("resource_checked_arithmetic_overflow", "checked u64 arithmetic overflow"),
    ("resource_platform_size_overflow", "checked platform-size conversion overflow"),
    ("resource_source_cap_exceeded", "authority source cap exceeded"),
    ("resource_section_cap_exceeded", "authority section cap exceeded"),
    ("resource_input_cap_exceeded", "serialized input cap exceeded"),
    ("resource_index_cap_exceeded", "retained index arena cap exceeded"),
    ("resource_output_cap_exceeded", "output cap exceeded"),
    ("resource_allocation_failed", "bounded allocation failed"),
)


class ErrorContractTests(unittest.TestCase):

    def test_exact_variant_order_messages_and_reason_codes(self):
        self.assertEqual(
            CONTRACT_FAILURE_VARIANTS,
            tuple(variant for variant, _ in EXPECTED_VARIANTS_AND_MESSAGES),
        )
        self.assertEqual(len(CONTRACT_FAILURE_VARIANTS), 48)
        self.assertEqual(
            tuple(CONTRACT_FAILURE_MESSAGES[variant] for variant in CONTRACT_FAILURE_VARIANTS),
            tuple(message for _, message in EXPECTED_VARIANTS_AND_MESSAGES),
        )
        self.assertEqual(
            tuple(CONTRACT_FAILURE_REASON_CODES[variant] for variant in CONTRACT_FAILURE_VARIANTS),
            CONTRACT_FAILURE_VARIANTS,
        )
        with self.assertRaises(TypeError):
            CONTRACT_FAILURE_MESSAGES[CONTRACT_FAILURE_VARIANTS[0]] = "changed"

    def test_exception_taxonomy_and_exact_precondition_reasons(self):
        for reason in ("permit_reuse", "compiler_identity_mismatch", "input_identity_mismatch"):
            error = CompilerPreconditionViolation(reason)
            self.assertEqual(error.reason, reason)
            self.assertEqual(str(error), reason)
            self.assertNotIsInstance(error, CompilerInternalError)
        with self.assertRaises(CompilerInternalError):
            CompilerPreconditionViolation("other")


class BoundBytesRefTests(unittest.TestCase):

    def assert_identity_mismatch(self, value):
        with self.assertRaises(CompilerPreconditionViolation) as raised:
            BoundBytesRef(value)
        self.assertEqual(raised.exception.reason, "input_identity_mismatch")

    def test_accepts_exact_bytes_and_bytes_exported_c_contiguous_views(self):
        raw = b"abcdefgh"
        admitted = (
            BoundBytesRef(raw),
            BoundBytesRef(memoryview(raw)),
            BoundBytesRef(memoryview(memoryview(raw))),
            BoundBytesRef(memoryview(raw).cast("B", shape=(2, 4))),
            BoundBytesRef(memoryview(raw)[2:6]),
        )
        self.assertEqual(tuple(item.nbytes for item in admitted), (8, 8, 8, 8, 4))
        multidimensional = admitted[3].buffer
        self.assertEqual(multidimensional.shape, (2, 4))
        self.assertEqual(multidimensional.strides, (4, 1))

    def test_rejects_nonadmitted_buffer_and_view_matrix(self):
        mutable = bytearray(b"abcdefgh")
        released = memoryview(b"x")
        released.release()
        rejected = (
            mutable,
            "bytes",
            memoryview(mutable),
            memoryview(mutable).toreadonly(),
            memoryview(b"abcdefgh")[::2],
            memoryview(b"abcdefgh").cast("I"),
            memoryview(b"x").cast("B", shape=()),
            released,
        )
        for value in rejected:
            with self.subTest(value=type(value).__name__):
                self.assert_identity_mismatch(value)


if __name__ == "__main__":
    unittest.main()
