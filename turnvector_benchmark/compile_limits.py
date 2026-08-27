"""Frozen checked-u64 CompileLimits contract (D0 implementation design).

This module is the strict compile-limits contract from
docs/D0-AUTHORITY-DESIGN.md ("Compile Limits", accepted design revision
3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a). Every
value below is the exact frozen value; none is silently changed. Integers are
positive checked u64 unless zero is explicitly permitted; no frozen limit
permits zero.

The canonical frozen instance is ``CompileLimits.frozen()``. All fields are
validated positive checked u64 at construction, and the checked arithmetic
helpers (``checked_add``, ``checked_mul``, ``platform_size``) validate every
operand as a NONNEGATIVE u64 (zero is allowed for arithmetic/conversion)
before any arithmetic or conversion and fail closed with
:class:`~turnvector_benchmark.core.ContractError` on an invalid operand or on
overflow, before any read, allocation, range arithmetic, or output
publication. The positive-only validator (``checked_u64``) is used only for
the frozen limit fields themselves and is never reused for arithmetic
operands, where zero is legal.

PR 5 enforces the compile-input/catalog/source/range/path/list caps needed
by the obligation-catalog loader and source verifier. The remaining caps are
exposed unchanged here for PR 6 (compiler), PR 7 (CompileCustody), and the
later catalog-content gate; raising or altering any value is a material
design change.

Accepted PR 5 contract amendment monotonicity rule (proposal revision
b068126b65ddaf8ea3f0f8ec9d1ced7409c3f545662864891e937d15cd8654b4, review
round TVB-AX-DETAIL-DESIGN-20260826-PR5A-R1-20260827T081347Z): a directly
constructed test limit vector L is valid only when every field is a positive
u64 and ``1 <= L_i <= F_i`` for every field i, where F is the exact
68-component frozen vector. Any component above F_i is rejected at
construction with ContractError. Loader/source-verifier injection of a
componentwise smaller L is permitted only as fail-closed testability: it can
reject an input accepted under F but can never admit an input rejected under
F. Accepted production compilation must use and bind exactly
``CompileLimits.frozen()``; PR 6 owns the exact-frozen equality check at the
compiler boundary, and ``is_frozen()`` here exposes the clear equality check.
Unrestricted custom limits are rejected because they allow a caller to bypass
accepted safety caps.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .core import ContractError

U64_MAX = (1 << 64) - 1


def checked_u64(value, where: str) -> int:
    """Validate *value* is a positive checked u64 and return it as int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where} must be an integer")
    if value < 1:
        raise ContractError(f"{where} must be a positive u64")
    if value > U64_MAX:
        raise ContractError(f"{where} exceeds the u64 range")
    return value


def checked_nonnegative_u64(value, where: str) -> int:
    """Validate *value* is a NONNEGATIVE checked u64 (zero allowed).

    Used for arithmetic/conversion operands, where zero is legal; it must
    never be used to validate the positive-only frozen limit fields.
    Rejects bools, non-integers, negative values, and values above u64.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where} must be an integer")
    if value < 0:
        raise ContractError(f"{where} must be a nonnegative u64")
    if value > U64_MAX:
        raise ContractError(f"{where} exceeds the u64 range")
    return value


def checked_add(left: int, right: int, where: str = "checked u64 addition") -> int:
    """Checked u64 addition; operands must be nonnegative u64 and overflow
    raises ContractError."""
    checked_nonnegative_u64(left, where)
    checked_nonnegative_u64(right, where)
    result = left + right
    if result > U64_MAX:
        raise ContractError(f"{where} overflows u64")
    return result


def checked_mul(left: int, right: int, where: str = "checked u64 multiplication") -> int:
    """Checked u64 multiplication; operands must be nonnegative u64 and
    overflow raises ContractError.

    Both operands are validated before the zero short-circuit, so a zero
    operand never masks an invalid one.
    """
    checked_nonnegative_u64(left, where)
    checked_nonnegative_u64(right, where)
    if left == 0 or right == 0:
        return 0
    if left > U64_MAX // right:
        raise ContractError(f"{where} overflows u64")
    return left * right


def platform_size(value: int, where: str = "platform size conversion") -> int:
    """Checked conversion of a NONNEGATIVE u64 measure to a platform size_t."""
    checked_nonnegative_u64(value, where)
    if value > sys.maxsize:
        raise ContractError(f"{where} exceeds the platform size")
    return value


@dataclass(frozen=True)
class CompileLimits:
    """Exact frozen compile limits; defaults are the accepted design values.

    ``CompileLimits.frozen()`` returns the canonical instance whose fields are
    exactly the accepted values from docs/D0-AUTHORITY-DESIGN.md (accepted
    design revision 3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a).
    A directly constructed instance is valid only for deterministic fail-closed
    tests and only when every field is a positive u64 that does not exceed that
    field's frozen default; any raised value is rejected at construction with
    :class:`~turnvector_benchmark.core.ContractError`. Componentwise smaller
    values never admit an input the frozen vector rejects. Accepted production
    compilation must use and bind exactly ``CompileLimits.frozen()``.
    """

    # Authority input caps.
    authority_file_count_max: int = 256
    authority_file_bytes_max: int = 4_194_304
    authority_total_bytes_max: int = 67_108_864
    authority_section_count_max: int = 1024
    authority_section_bytes_total_max: int = 33_554_432
    serialized_input_bytes_total_max: int = 16_777_216
    # Catalog/entity count caps.
    catalog_record_count_max: int = 512
    case_plan_count_max: int = 4096
    obligation_count_max: int = 256
    judge_count_max: int = 256
    evidence_bundle_count_max: int = 64
    evidence_source_count_max: int = 256
    gate_count_max: int = 256
    negative_test_class_count_max: int = 256
    # Path and relation degree caps.
    path_count_max: int = 32_768
    paths_per_obligation_max: int = 1024  # d_H
    evidence_members_per_bundle_or_path_max: int = 16  # d_DE
    context_artifacts_per_lane_max: int = 2  # d_LC
    post_gate_outputs_per_lane_max: int = 2  # d_LP
    paths_per_case_max: int = 32  # d_CH
    paths_per_gate_max: int = 512  # d_GH
    judge_negative_tests_per_path_max: int = 1  # d_HNJ
    aggregate_negative_tests_per_gate_max: int = 1  # d_GNG
    plumbing_negative_tests_per_gate_max: int = 1  # d_GNP
    # Buffers, parser, arena, and output caps.
    authority_hash_buffer_max: int = 65_536  # h_max
    largest_single_serialized_parser_input_max: int = 4_194_304  # l_max
    logical_retained_index_arena_max: int = 33_554_432  # x_max
    output_streaming_chunk_max: int = 65_536  # q_max
    coverage_plan_or_failure_max: int = 33_554_432  # p_max
    per_receipt_max: int = 65_536
    # Custody object caps.
    custody_domain_record_max: int = 32_768
    custody_registry_genesis_max: int = 32_768
    custody_registry_binding_event_max: int = 16_384
    compile_chronology_genesis_max: int = 32_768
    compile_chronology_event_max: int = 16_384
    custody_event_staging_file_max: int = 16_384
    attempt_object_staging_directory_max: int = 33_619_968
    interruption_quarantine_max: int = 33_619_968
    # Attempt cap.
    t_max: int = 8
    # Execution-closure caps.
    execution_closure_record_count_max: int = 4_096
    execution_closure_file_bytes_max: int = 83_886_080
    execution_closure_path_bytes_max: int = 4_096
    execution_closure_path_bytes_total_max: int = 16_777_216
    execution_closure_symlink_target_bytes_max: int = 4_096
    execution_closure_symlink_target_bytes_total_max: int = 16_777_216
    execution_closure_loaded_image_count_max: int = 512
    execution_closure_loaded_image_path_bytes_max: int = 4_096
    execution_closure_loaded_image_path_bytes_total_max: int = 2_097_152
    # Repository-control caps.
    canonical_directory_entry_count_max: int = 4_096
    canonical_directory_name_bytes_max: int = 4_194_304
    canonical_directory_sort_index_bytes_max: int = 65_536
    repository_control_entry_count_max: int = 32_768
    repository_control_path_bytes_max: int = 4_096
    repository_control_path_bytes_total_max: int = 134_217_728
    repository_control_file_bytes_max: int = 4_194_304
    repository_control_file_bytes_total_max: int = 33_554_432
    repository_control_config_bytes_total_max: int = 1_048_576
    repository_control_ignore_file_count_max: int = 256
    repository_control_ignore_file_bytes_max: int = 1_048_576
    repository_control_ignore_bytes_total_max: int = 4_194_304
    repository_control_git_path_record_count_max: int = 32_768
    repository_control_git_output_bytes_max: int = 134_217_728
    repository_control_git_output_bytes_other_max: int = 4_194_304
    repository_control_git_stderr_bytes_max: int = 1_048_576
    repository_control_git_timeout_seconds: int = 60
    # Authority child-process caps.
    authority_child_stdout_bytes_max: int = 33_652_736
    authority_child_stderr_bytes_max: int = 1_048_576
    authority_child_timeout_seconds: int = 600

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__.values():
            value = getattr(self, field.name)
            checked_u64(value, f"CompileLimits.{field.name}")
            if value > field.default:
                raise ContractError(
                    f"CompileLimits.{field.name} exceeds the frozen default "
                    f"{field.default}"
                )

    @classmethod
    def frozen(cls) -> "CompileLimits":
        """Return the canonical instance carrying the exact frozen values."""
        return cls()

    def is_frozen(self) -> bool:
        """True iff every field exactly equals the accepted frozen vector F.

        Exposes the clear exact-frozen equality check for the PR 6 compiler
        boundary, which must bind and use exactly ``CompileLimits.frozen()``.
        """
        return all(
            getattr(self, field.name) == field.default
            for field in self.__dataclass_fields__.values()
        )
