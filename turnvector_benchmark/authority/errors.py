"""CoverageCompiler exception taxonomy and frozen ContractFailure variants."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final


PRECONDITION_REASONS: Final = (
    "permit_reuse",
    "compiler_identity_mismatch",
    "input_identity_mismatch",
)


class CompilerPreconditionViolation(ValueError):
    """A custody/compiler-boundary precondition was violated."""

    def __init__(self, reason: str) -> None:
        if reason not in PRECONDITION_REASONS:
            raise CompilerInternalError("unknown compiler precondition reason")
        self.reason = reason
        super().__init__(reason)


class CompilerInternalError(RuntimeError):
    """The sole named exception for CoverageCompiler internal invariants."""


CONTRACT_FAILURE_VARIANTS: Final = (
    "authority_invalid_canonical_json",
    "authority_unknown_field",
    "authority_duplicate_key",
    "authority_invalid_identifier",
    "authority_duplicate_identifier",
    "authority_order_violation",
    "authority_absolute_path",
    "authority_path_traversal",
    "authority_symlink",
    "authority_non_regular_file",
    "authority_missing_source",
    "authority_missing_historical_object",
    "authority_repository_mismatch",
    "authority_revision_mismatch",
    "authority_dirty_repository",
    "authority_file_digest_mismatch",
    "authority_invalid_section_range",
    "authority_section_digest_mismatch",
    "authority_repository_control_unsupported",
    "catalog_digest_mismatch",
    "catalog_gate_revision_mismatch",
    "catalog_lineage_mismatch",
    "catalog_predecessor_mismatch",
    "catalog_record_limit_exceeded",
    "catalog_orphan_source",
    "catalog_invalid_status",
    "expectation_digest_mismatch",
    "traceability_digest_mismatch",
    "traceability_unknown_entity",
    "traceability_duplicate_entity",
    "traceability_orphan_obligation",
    "traceability_orphan_case",
    "traceability_orphan_judge",
    "traceability_orphan_bundle",
    "traceability_orphan_gate",
    "traceability_orphan_evidence",
    "traceability_relation_mismatch",
    "traceability_degree_cap_exceeded",
    "traceability_empty_required_set",
    "traceability_negative_test_coverage_missing",
    "resource_checked_arithmetic_overflow",
    "resource_platform_size_overflow",
    "resource_source_cap_exceeded",
    "resource_section_cap_exceeded",
    "resource_input_cap_exceeded",
    "resource_index_cap_exceeded",
    "resource_output_cap_exceeded",
    "resource_allocation_failed",
)

_MESSAGES = (
    "authority input is not strict canonical JSON",
    "authority input contains an unknown, missing, or mistyped field",
    "authority input contains a duplicate object key",
    "authority input contains an invalid identifier, enum, or typed scalar",
    "authority input declares a duplicate scalar identifier",
    "authority input violates canonical order or byte re-encoding",
    "authority input contains an absolute path",
    "authority input contains a path traversal",
    "authority snapshot claims a symlink",
    "authority snapshot claims a non-regular file",
    "catalog source has no snapshot file record",
    "historical object set disagrees with the reconciliation mappings",
    "repository identity or clean-required status disagrees across authority inputs",
    "revision identity disagrees within or across authority inputs",
    "required clean repository is dirty",
    "authority file or historical-object digest disagrees with the catalog or reconciliation",
    "section range is invalid or the section set mismatches",
    "section digest disagrees with the catalog",
    "repository-control descriptor is unsupported",
    "ledger catalog digest disagrees with the catalog input",
    "catalog design-gate revision disagrees with the header or ledger",
    "custody lineage binding disagrees with the permit",
    "catalog predecessor disagrees with the ledger or the current lineage",
    "catalog record count exceeds the limit or mismatches",
    "snapshot source file is not cited by any obligation",
    "catalog readiness and blocker algebra is invalid",
    "expectation digest disagrees with the catalog or ledger",
    "traceability bind digest disagrees with an input",
    "traceability references an unknown entity",
    "traceability contains a duplicate entity or relation record",
    "required obligation is orphaned",
    "expectation behavior case is orphaned",
    "judge is orphaned",
    "evidence bundle is orphaned",
    "gate is orphaned",
    "evidence source is orphaned",
    "traceability relation or count mismatches",
    "traceability degree cap is exceeded",
    "a required traceability set is empty",
    "negative-test coverage is missing",
    "checked u64 arithmetic overflow",
    "checked platform-size conversion overflow",
    "authority source cap exceeded",
    "authority section cap exceeded",
    "serialized input cap exceeded",
    "retained index arena cap exceeded",
    "output cap exceeded",
    "bounded allocation failed",
)

CONTRACT_FAILURE_MESSAGES: Final = MappingProxyType(
    dict(zip(CONTRACT_FAILURE_VARIANTS, _MESSAGES))
)
# Each frozen reason code is exactly its variant name.
CONTRACT_FAILURE_REASON_CODES: Final = MappingProxyType(
    {variant: variant for variant in CONTRACT_FAILURE_VARIANTS}
)
