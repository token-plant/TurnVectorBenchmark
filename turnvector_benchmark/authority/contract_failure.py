"""Immutable canonical ``ContractFailure`` output value and builder.

Failure materialization intentionally performs no allocation-failure recovery:
a host ``MemoryError`` propagates unchanged and never recursively becomes the
``resource_allocation_failed`` variant.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Tuple

from ..core import ContractError
from .coverage_plan import (
    AUTHORITY_PATHS,
    FrozenDict,
    PROFILE_ID,
    T_MAX,
    _freeze,
    _identifier,
    _plain,
    _sha256,
    _strict_mapping,
    _u64,
    canonical_object_bytes,
)

SCHEMA_VERSION = "turnvector.benchmark.contract-failure.v1"
OUTCOME = "contract_failure"
DIAGNOSTIC_COUNT_MAX = 64
DIAGNOSTIC_BYTES_MAX = 1024
MESSAGE_BYTES_MAX = 1024

# Immutable public ordinal from V28 section 10.  The position is the stage.
CONTRACT_FAILURE_VARIANTS = (
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
CONTRACT_FAILURE_MESSAGES = FrozenDict(dict(zip(CONTRACT_FAILURE_VARIANTS, _MESSAGES)))

_FAILURE_INPUT_FIELDS = (
    "source_reconciliation_path", "source_reconciliation_sha256",
    "expectation_path", "expectation_sha256", "catalog_path", "catalog_sha256",
    "traceability_path", "traceability_sha256", "authority_snapshot_sha256",
    "compile_limits_sha256", "input_set_sha256",
)
_ERROR_FIELDS = ("variant", "message", "diagnostics", "discarded_diagnostic_count")
_OBSERVED_FIELDS = (
    "authority_file_count", "authority_byte_count", "section_count", "section_byte_count",
    "serialized_input_byte_count", "catalog_record_count", "entity_count",
    "relation_record_count", "endpoint_reference_count", "path_count",
    "logical_arena_byte_count", "output_byte_count_attempted",
)


def _validate_failure_inputs(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _FAILURE_INPUT_FIELDS, "inputs")
    for name, expected in AUTHORITY_PATHS.items():
        if obj[name] != expected:
            raise ContractError(f"inputs.{name} must equal {expected!r}")
    for name in _FAILURE_INPUT_FIELDS:
        if name.endswith("_sha256"):
            _sha256(obj[name], f"inputs.{name}")
    return _freeze(obj, "inputs")


def _validate_diagnostic(value: Any, variant: str, where: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{where} must be a string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ContractError(f"{where} contains an unpaired surrogate") from error
    if size > DIAGNOSTIC_BYTES_MAX:
        raise ContractError(f"{where} exceeds {DIAGNOSTIC_BYTES_MAX} UTF-8 bytes")
    prefix = variant + "|"
    suffix = "|" + variant
    if not value.startswith(prefix) or not value.endswith(suffix):
        raise ContractError(f"{where} does not carry the primary variant identity")
    pointer = value[len(prefix):-len(suffix)]
    if not pointer.startswith("/"):
        raise ContractError(f"{where} must contain an RFC 6901 pointer")
    return value


def _validate_error(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _ERROR_FIELDS, "error")
    variant = obj["variant"]
    if variant not in CONTRACT_FAILURE_VARIANTS:
        raise ContractError("error.variant is not in the exact 48-variant ordinal")
    if obj["message"] != CONTRACT_FAILURE_MESSAGES[variant]:
        raise ContractError("error.message is not the fixed message for its variant")
    if len(obj["message"].encode("ascii")) > MESSAGE_BYTES_MAX:
        raise ContractError("error.message exceeds its bound")
    diagnostics = obj["diagnostics"]
    if not isinstance(diagnostics, (list, tuple)) or len(diagnostics) > DIAGNOSTIC_COUNT_MAX:
        raise ContractError("error.diagnostics exceeds the 64-entry bound")
    for index, diagnostic in enumerate(diagnostics):
        _validate_diagnostic(diagnostic, variant, f"error.diagnostics[{index}]")
    _u64(obj["discarded_diagnostic_count"], "error.discarded_diagnostic_count")
    return _freeze(obj, "error")


def _validate_observed(value: Any) -> FrozenDict:
    obj = _strict_mapping(value, _OBSERVED_FIELDS, "observed")
    for name in _OBSERVED_FIELDS:
        _u64(obj[name], f"observed.{name}")
    return _freeze(obj, "observed")


def build_error(
    variant: str,
    diagnostics: Sequence[str] = (),
    discarded_diagnostic_count: int = 0,
) -> FrozenDict:
    """Build the exact bounded error object, dropping rather than truncating.

    Eligible diagnostics retain caller/pipeline order.  An oversize diagnostic
    or an eligible diagnostic after the first 64 increments the checked
    discarded count.  Malformed in-bound diagnostics are contract errors.
    """
    if variant not in CONTRACT_FAILURE_VARIANTS:
        raise ContractError("variant is not in the exact 48-variant ordinal")
    discarded = _u64(discarded_diagnostic_count, "discarded_diagnostic_count")
    kept = []
    for index, diagnostic in enumerate(diagnostics):
        if not isinstance(diagnostic, str):
            raise ContractError(f"diagnostics[{index}] must be a string")
        try:
            size = len(diagnostic.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ContractError(f"diagnostics[{index}] contains an unpaired surrogate") from error
        if size > DIAGNOSTIC_BYTES_MAX or len(kept) == DIAGNOSTIC_COUNT_MAX:
            discarded = _u64(discarded + 1, "discarded_diagnostic_count")
            continue
        kept.append(_validate_diagnostic(diagnostic, variant, f"diagnostics[{index}]"))
    return _validate_error({
        "variant": variant,
        "message": CONTRACT_FAILURE_MESSAGES[variant],
        "diagnostics": kept,
        "discarded_diagnostic_count": discarded,
    })


@dataclass(frozen=True)
class ContractFailure:
    schema_version: str
    profile_id: str
    outcome: str
    custody_domain_id: str
    custody_domain_sha256: str
    custody_lineage_id: str
    attempt: int
    t_max: int
    start_event_sha256: str
    chronology_prefix_sha256: str
    chronology_prefix_byte_count: int
    compiler_build_sha256: str
    execution_closure_sha256: str
    compile_custody_policy_sha256: str
    input_set_sha256: str
    inputs: Mapping[str, Any]
    error: Mapping[str, Any]
    observed: Mapping[str, Any]
    _canonical: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.profile_id != PROFILE_ID or self.outcome != OUTCOME:
            raise ContractError("ContractFailure identity constants are invalid")
        _identifier(self.custody_domain_id, "custody_domain_id")
        _identifier(self.custody_lineage_id, "custody_lineage_id")
        for name in ("custody_domain_sha256", "start_event_sha256", "chronology_prefix_sha256",
                     "compiler_build_sha256", "execution_closure_sha256",
                     "compile_custody_policy_sha256", "input_set_sha256"):
            _sha256(getattr(self, name), name)
        for name in ("attempt", "t_max", "chronology_prefix_byte_count"):
            _u64(getattr(self, name), name)
        if not 1 <= self.attempt <= self.t_max or self.t_max != T_MAX:
            raise ContractError("ContractFailure attempt/t_max is invalid")
        inputs = _validate_failure_inputs(self.inputs)
        if inputs["input_set_sha256"] != self.input_set_sha256:
            raise ContractError("failure input_set_sha256 projections disagree")
        error = _validate_error(self.error)
        observed = _validate_observed(self.observed)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "observed", observed)
        # Do not catch MemoryError: materialization is outside the modeled inventory.
        object.__setattr__(self, "_canonical", canonical_object_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {name: _plain(getattr(self, name)) for name in (
            "schema_version", "profile_id", "outcome", "custody_domain_id",
            "custody_domain_sha256", "custody_lineage_id", "attempt", "t_max",
            "start_event_sha256", "chronology_prefix_sha256", "chronology_prefix_byte_count",
            "compiler_build_sha256", "execution_closure_sha256",
            "compile_custody_policy_sha256", "input_set_sha256", "inputs", "error", "observed",
        )}

    @property
    def canonical_bytes(self) -> bytes:
        return self._canonical

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._canonical).hexdigest()

    @property
    def byte_count(self) -> int:
        return len(self._canonical)

    @classmethod
    def build(
        cls,
        *,
        variant: str,
        diagnostics: Sequence[str] = (),
        discarded_diagnostic_count: int = 0,
        **fields: Any,
    ) -> "ContractFailure":
        fields.setdefault("schema_version", SCHEMA_VERSION)
        fields.setdefault("profile_id", PROFILE_ID)
        fields.setdefault("outcome", OUTCOME)
        fields["error"] = build_error(variant, diagnostics, discarded_diagnostic_count)
        return cls(**fields)


def build_contract_failure(**fields: Any) -> ContractFailure:
    """Build and fully validate one immutable canonical ContractFailure."""
    return ContractFailure.build(**fields)
