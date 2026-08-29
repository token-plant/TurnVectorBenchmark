"""Checked resource accounting, arena, scratch, and allocation-order tests."""

import unittest

from turnvector_benchmark.authority.compiler_accounting import (
    HASH_BUFFER_CHARGE,
    SHA256_STATE_CHARGE,
    SWEEP_COUNTER_COUNT,
    W_B,
    OutputSink,
    PhaseBScratch,
    RetainedArena,
    SharedHashBuffer,
    _FaultInjector,
    derived_record_charge,
    enforcement_path_charge,
    intern_entry_charge,
    sort_index_charge,
)
from turnvector_benchmark.authority.errors import CompilerInternalError
from turnvector_benchmark.core import ContractError


class CompilerResourceTests(unittest.TestCase):
    def test_golden_unit_charges(self):
        self.assertEqual(W_B, 2712)
        self.assertEqual(SWEEP_COUNTER_COUNT * 8, W_B)
        self.assertEqual(SHA256_STATE_CHARGE, 256)
        self.assertEqual(HASH_BUFFER_CHARGE, 65536)
        for endpoints in range(1, 7):
            self.assertEqual(derived_record_charge(endpoints), 8 + 8 * endpoints)
        self.assertEqual(derived_record_charge(2), 24)
        self.assertEqual(intern_entry_charge("abc"), 19)
        self.assertEqual(sort_index_charge(58), 928)

    def test_enforcement_path_charge_uses_five_slots_plus_record_header_and_path_bytes(self):
        path = "a~b~c~d~e"
        self.assertEqual(enforcement_path_charge(path), 40 + 8 + len(path))

    def test_arena_exact_and_one_past_boundary(self):
        arena = RetainedArena(10)
        self.assertTrue(arena.reserve(10))
        self.assertEqual((arena.used, arena.high_water), (10, 10))
        self.assertFalse(arena.reserve(1))
        self.assertEqual(arena.used, 10)
        self.assertEqual(arena.last_candidate, 11)
        self.assertEqual(arena.high_water, 11)
        arena.release(10)
        self.assertEqual(arena.used, 0)
        with self.assertRaises(CompilerInternalError):
            arena.release(1)

    def test_modeled_allocation_fault_is_one_shot_and_precommit(self):
        arena = RetainedArena(100)
        fault = _FaultInjector(2)
        first = arena.modeled_allocate(10, "first", object, fault)
        self.assertIsNotNone(first)
        self.assertEqual(arena.used, 10)
        with self.assertRaises(MemoryError):
            arena.modeled_allocate(20, "second", object, fault)
        self.assertEqual(arena.used, 10)
        self.assertTrue(fault.fired)
        third = arena.modeled_allocate(5, "third", object, fault)
        self.assertIsNotNone(third)
        self.assertEqual((fault.ordinal, arena.used), (3, 15))

    def test_s8_states_are_sequentially_released(self):
        arena = RetainedArena()
        for _ in range(5):
            self.assertIsNotNone(arena.retain_s8_hash_state())
            self.assertEqual(arena.used, 256)
            arena.release(256)
            self.assertEqual(arena.used, 0)
        self.assertEqual(arena.high_water, 256)

    def test_phase_b_single_slab_regions_never_add_arena_charge(self):
        arena = RetainedArena()
        scratch = PhaseBScratch.allocate(arena)
        self.assertEqual(arena.used, W_B)
        scratch.clear_counter_block()
        self.assertEqual(scratch.scratch_used, W_B)
        scratch.set_u64(338, 7)
        self.assertEqual(scratch.get_u64(338), 7)
        scratch.clear_mapping_table(58)
        self.assertEqual(scratch.scratch_used, 464)
        self.assertEqual(arena.used, W_B)
        with self.assertRaises(CompilerInternalError):
            scratch.get_u64(58)

    def test_case_id_hit_has_zero_charge_and_no_fault_ordinal(self):
        arena = RetainedArena()
        interned = {b"case.id.0001"}
        fault = _FaultInjector(1)
        self.assertEqual(arena.visit_case_id("case.id.0001", interned, fault), (True, False))
        self.assertEqual((arena.used, fault.ordinal, fault.fired), (0, 0, False))
        with self.assertRaises(MemoryError):
            arena.visit_case_id("case.id.0002", interned, fault)
        self.assertEqual((arena.used, fault.ordinal), (0, 1))

    def test_shared_hash_buffer_has_one_physical_allocation_two_logical_uses(self):
        arena = RetainedArena(200000)
        fault = _FaultInjector(2)
        shared = SharedHashBuffer.allocate_phase_a(arena, fault)
        self.assertIsNotNone(shared)
        self.assertEqual((fault.ordinal, arena.used), (1, HASH_BUFFER_CHARGE))
        shared.logical_release(arena)
        self.assertTrue(shared.logical_reserve_phase_d(arena))
        self.assertEqual(fault.ordinal, 1, "Phase D logical reuse must not allocate")
        shared.logical_release(arena)

    def test_checked_helpers_reject_negative_bool_and_overflow(self):
        for function, args in ((derived_record_charge, (-1,)),
                               (intern_entry_charge, (object(),)),
                               (sort_index_charge, (-1,))):
            with self.subTest(function=function.__name__):
                with self.assertRaises((ContractError, TypeError, ValueError, CompilerInternalError)):
                    function(*args)


if __name__ == "__main__":
    unittest.main()
