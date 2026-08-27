"""Tests for the frozen checked-u64 CompileLimits contract."""

import sys
import unittest

from turnvector_benchmark.compile_limits import (
    U64_MAX,
    CompileLimits,
    checked_add,
    checked_mul,
    checked_u64,
    platform_size,
)
from turnvector_benchmark.core import ContractError

# Exact frozen values from docs/D0-AUTHORITY-DESIGN.md ("Compile Limits",
# accepted design revision 3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a).
# Any change to these values is a material design change.
_EXPECTED = {
    "authority_file_count_max": 256,
    "authority_file_bytes_max": 4_194_304,
    "authority_total_bytes_max": 67_108_864,
    "authority_section_count_max": 1024,
    "authority_section_bytes_total_max": 33_554_432,
    "serialized_input_bytes_total_max": 16_777_216,
    "catalog_record_count_max": 512,
    "case_plan_count_max": 4096,
    "obligation_count_max": 256,
    "judge_count_max": 256,
    "evidence_bundle_count_max": 64,
    "evidence_source_count_max": 256,
    "gate_count_max": 256,
    "negative_test_class_count_max": 256,
    "path_count_max": 32_768,
    "paths_per_obligation_max": 1024,
    "evidence_members_per_bundle_or_path_max": 16,
    "context_artifacts_per_lane_max": 2,
    "post_gate_outputs_per_lane_max": 2,
    "paths_per_case_max": 32,
    "paths_per_gate_max": 512,
    "judge_negative_tests_per_path_max": 1,
    "aggregate_negative_tests_per_gate_max": 1,
    "plumbing_negative_tests_per_gate_max": 1,
    "authority_hash_buffer_max": 65_536,
    "largest_single_serialized_parser_input_max": 4_194_304,
    "logical_retained_index_arena_max": 33_554_432,
    "output_streaming_chunk_max": 65_536,
    "coverage_plan_or_failure_max": 33_554_432,
    "per_receipt_max": 65_536,
    "custody_domain_record_max": 32_768,
    "custody_registry_genesis_max": 32_768,
    "custody_registry_binding_event_max": 16_384,
    "compile_chronology_genesis_max": 32_768,
    "compile_chronology_event_max": 16_384,
    "custody_event_staging_file_max": 16_384,
    "attempt_object_staging_directory_max": 33_619_968,
    "interruption_quarantine_max": 33_619_968,
    "t_max": 8,
    "execution_closure_record_count_max": 4_096,
    "execution_closure_file_bytes_max": 83_886_080,
    "execution_closure_path_bytes_max": 4_096,
    "execution_closure_path_bytes_total_max": 16_777_216,
    "execution_closure_symlink_target_bytes_max": 4_096,
    "execution_closure_symlink_target_bytes_total_max": 16_777_216,
    "execution_closure_loaded_image_count_max": 512,
    "execution_closure_loaded_image_path_bytes_max": 4_096,
    "execution_closure_loaded_image_path_bytes_total_max": 2_097_152,
    "canonical_directory_entry_count_max": 4_096,
    "canonical_directory_name_bytes_max": 4_194_304,
    "canonical_directory_sort_index_bytes_max": 65_536,
    "repository_control_entry_count_max": 32_768,
    "repository_control_path_bytes_max": 4_096,
    "repository_control_path_bytes_total_max": 134_217_728,
    "repository_control_file_bytes_max": 4_194_304,
    "repository_control_file_bytes_total_max": 33_554_432,
    "repository_control_config_bytes_total_max": 1_048_576,
    "repository_control_ignore_file_count_max": 256,
    "repository_control_ignore_file_bytes_max": 1_048_576,
    "repository_control_ignore_bytes_total_max": 4_194_304,
    "repository_control_git_path_record_count_max": 32_768,
    "repository_control_git_output_bytes_max": 134_217_728,
    "repository_control_git_output_bytes_other_max": 4_194_304,
    "repository_control_git_stderr_bytes_max": 1_048_576,
    "repository_control_git_timeout_seconds": 60,
    "authority_child_stdout_bytes_max": 33_652_736,
    "authority_child_stderr_bytes_max": 1_048_576,
    "authority_child_timeout_seconds": 600,
}


class CompileLimitsFrozenValuesTests(unittest.TestCase):

    def test_frozen_instance_matches_every_accepted_value(self):
        limits = CompileLimits.frozen()
        for name, expected in _EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(limits, name), expected)
        self.assertEqual(len(limits.__dataclass_fields__), len(_EXPECTED))

    def test_all_fields_are_positive(self):
        limits = CompileLimits.frozen()
        for name, value in limits.__dataclass_fields__.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(getattr(limits, name), 1)

    def test_frozen_is_immutable_and_hashable(self):
        limits = CompileLimits.frozen()
        with self.assertRaises(Exception):
            limits.authority_file_bytes_max = 1  # frozen dataclass
        self.assertEqual(limits, CompileLimits.frozen())
        self.assertEqual(hash(limits), hash(CompileLimits.frozen()))


class CompileLimitsValidationTests(unittest.TestCase):

    def test_zero_rejected(self):
        with self.assertRaisesRegex(ContractError, "positive"):
            CompileLimits(authority_file_bytes_max=0)

    def test_negative_rejected(self):
        with self.assertRaisesRegex(ContractError, "positive"):
            CompileLimits(t_max=-1)

    def test_overflow_u64_rejected(self):
        with self.assertRaisesRegex(ContractError, "u64"):
            CompileLimits(obligation_count_max=U64_MAX + 1)

    def test_bool_rejected_as_integer(self):
        with self.assertRaisesRegex(ContractError, "integer"):
            CompileLimits(gate_count_max=True)

    def test_float_rejected(self):
        with self.assertRaisesRegex(ContractError, "integer"):
            CompileLimits(path_count_max=1.0)

    def test_checked_u64_accepts_boundary_values(self):
        self.assertEqual(checked_u64(1, "x"), 1)
        self.assertEqual(checked_u64(U64_MAX, "x"), U64_MAX)
        with self.assertRaisesRegex(ContractError, "positive"):
            checked_u64(0, "x")
        with self.assertRaisesRegex(ContractError, "u64"):
            checked_u64(U64_MAX + 1, "x")
        with self.assertRaisesRegex(ContractError, "integer"):
            checked_u64(True, "x")


class CompileLimitsMonotonicInjectionTests(unittest.TestCase):
    """A constructed vector must be componentwise <= the frozen vector F.

    Smaller values are the only permitted injection and are fail-closed: they
    can reject an input accepted under F but can never admit one F rejects.
    Any raised component is rejected at construction.
    """

    def test_frozen_instance_is_frozen(self):
        self.assertTrue(CompileLimits.frozen().is_frozen())
        self.assertEqual(CompileLimits.frozen(), CompileLimits.frozen())

    def test_componentwise_smaller_value_allowed(self):
        limits = CompileLimits(catalog_record_count_max=3, obligation_count_max=16)
        self.assertEqual(limits.catalog_record_count_max, 3)
        self.assertEqual(limits.obligation_count_max, 16)
        self.assertFalse(limits.is_frozen())
        # Every other field stays exactly at its frozen default.
        frozen = CompileLimits.frozen()
        for name in limits.__dataclass_fields__:
            if name in ("catalog_record_count_max", "obligation_count_max"):
                continue
            with self.subTest(name=name):
                self.assertEqual(getattr(limits, name), getattr(frozen, name))

    def test_equal_to_frozen_default_allowed(self):
        frozen = CompileLimits.frozen()
        same = CompileLimits(obligation_count_max=frozen.obligation_count_max)
        self.assertTrue(same.is_frozen())

    def test_raised_value_rejected_at_construction(self):
        frozen = CompileLimits.frozen()
        cases = {
            "obligation_count_max": frozen.obligation_count_max + 1,
            "catalog_record_count_max": frozen.catalog_record_count_max + 1,
            "t_max": frozen.t_max + 1,
            "largest_single_serialized_parser_input_max": (
                frozen.largest_single_serialized_parser_input_max + 1
            ),
        }
        for name, value in cases.items():
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(ContractError, "frozen default"):
                    CompileLimits(**{name: value})

    def test_raised_every_field_variants_rejected(self):
        # bool/zero/negative/non-int/>u64 are already covered; a raised
        # positive value is the injection-specific rejection.
        with self.assertRaisesRegex(ContractError, "frozen default"):
            CompileLimits(authority_file_bytes_max=CompileLimits.frozen().authority_file_bytes_max + 1)


class CompileLimitsArithmeticTests(unittest.TestCase):

    def test_checked_add(self):
        self.assertEqual(checked_add(40, 2), 42)
        self.assertEqual(checked_add(0, 0), 0)
        self.assertEqual(checked_add(U64_MAX - 1, 1), U64_MAX)
        with self.assertRaisesRegex(ContractError, "overflow"):
            checked_add(U64_MAX, 1)

    def test_checked_add_rejects_invalid_operands(self):
        # Negative, bool, float, >u64, and non-int operands are invalid even
        # when the other operand is a legal zero.
        for bad in (-1, True, 1.5, U64_MAX + 1, "8", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    checked_add(bad, 0)
                with self.assertRaises(ContractError):
                    checked_add(0, bad)
                with self.assertRaises(ContractError):
                    checked_add(bad, bad)

    def test_checked_mul(self):
        self.assertEqual(checked_mul(6, 7), 42)
        self.assertEqual(checked_mul(0, U64_MAX), 0)
        self.assertEqual(checked_mul(U64_MAX, 1), U64_MAX)
        with self.assertRaisesRegex(ContractError, "overflow"):
            checked_mul(U64_MAX, 2)

    def test_checked_mul_validates_both_operands_before_zero_short_circuit(self):
        # A zero operand must not mask an invalid one.
        for bad in (-1, True, 1.5, U64_MAX + 1, "8", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    checked_mul(bad, 0)
                with self.assertRaises(ContractError):
                    checked_mul(0, bad)
                with self.assertRaises(ContractError):
                    checked_mul(bad, bad)

    def test_platform_size(self):
        self.assertEqual(platform_size(sys.maxsize), sys.maxsize)
        with self.assertRaisesRegex(ContractError, "platform size"):
            platform_size(sys.maxsize + 1)

    def test_platform_size_zero_allowed(self):
        self.assertEqual(platform_size(0), 0)

    def test_platform_size_rejects_invalid_operands(self):
        for bad in (-1, True, 1.5, U64_MAX + 1, "8", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    platform_size(bad)


if __name__ == "__main__":
    unittest.main()
