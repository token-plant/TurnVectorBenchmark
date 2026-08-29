"""Frozen logical accounting, arena, fault, scratch, and output contracts."""

from __future__ import annotations

import struct
from dataclasses import dataclass, fields, replace
from typing import Any, Callable, Final, Optional, Tuple, TypeVar

from turnvector_benchmark.compile_limits import (
    CompileLimits,
    checked_add,
    checked_mul,
    checked_nonnegative_u64,
    platform_size,
)
from turnvector_benchmark.core import ContractError

from .errors import CompilerInternalError

W_B: Final = 2_712
SWEEP_COUNTER_COUNT: Final = 339
JUDGE_MAPPING_COUNT: Final = 46
GATE_MAPPING_COUNT: Final = 58
SHA256_STATE_CHARGE: Final = 256
HASH_BUFFER_CHARGE: Final = 65_536
DERIVED_CASE_ID_VISIT_COUNT: Final = 425

_T = TypeVar("_T")


@dataclass(frozen=True)
class Accounting:
    """Checked-u64 observed-resource counters used by checkpoints K0--K4."""

    authority_file_count: int = 0
    authority_byte_count: int = 0
    section_count: int = 0
    section_byte_count: int = 0
    serialized_input_byte_count: int = 0
    catalog_record_count: int = 0
    entity_count: int = 0
    relation_record_count: int = 0
    endpoint_reference_count: int = 0
    path_count: int = 0
    logical_arena_byte_count: int = 0
    output_byte_count_attempted: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            checked_nonnegative_u64(getattr(self, field.name), "Accounting." + field.name)

    def with_value(self, name: str, value: int) -> "Accounting":
        if name not in self.__dataclass_fields__:
            raise CompilerInternalError("unknown accounting counter")
        checked_nonnegative_u64(value, "Accounting." + name)
        return replace(self, **{name: value})

    def increment(self, name: str, amount: int = 1) -> "Accounting":
        if name not in self.__dataclass_fields__:
            raise CompilerInternalError("unknown accounting counter")
        value = checked_add(getattr(self, name), amount, "Accounting." + name)
        return replace(self, **{name: value})


def derived_record_charge(endpoint_arity: int) -> int:
    """Exact retained-derived-record algebra: one slot plus k references."""
    return checked_add(8, checked_mul(8, endpoint_arity, "derived endpoint charge"))


def intern_entry_charge(identifier: Any) -> int:
    """Exact intern miss charge: 16 plus UTF-8 identifier bytes."""
    if isinstance(identifier, str):
        try:
            length = len(identifier.encode("utf-8"))
        except UnicodeError as error:
            raise CompilerInternalError("identifier is not valid UTF-8") from error
    elif type(identifier) is bytes:
        length = len(identifier)
    else:
        raise CompilerInternalError("intern key must be str or exact bytes")
    return checked_add(16, length, "intern entry charge")


def enforcement_path_charge(path_id: Any) -> int:
    """Exact EnforcementPath charge: 48 fixed bytes plus path-id bytes."""
    if isinstance(path_id, str):
        length = len(path_id.encode("utf-8"))
    elif type(path_id) is bytes:
        length = len(path_id)
    else:
        raise CompilerInternalError("path id must be str or exact bytes")
    return checked_add(48, length, "EnforcementPath charge")


def sort_index_charge(record_count: int) -> int:
    return checked_mul(16, record_count, "sort index charge")


class _FaultInjector:
    """Private deterministic one-shot seam over modeled allocation ordinals."""

    def __init__(self, target_ordinal: Optional[int] = None) -> None:
        if target_ordinal is not None and (
            isinstance(target_ordinal, bool)
            or not isinstance(target_ordinal, int)
            or target_ordinal < 1
        ):
            raise CompilerInternalError("fault target must be a positive ordinal")
        self._target_ordinal = target_ordinal
        self._ordinal = 0
        self._fired = False

    @property
    def ordinal(self) -> int:
        return self._ordinal

    @property
    def fired(self) -> bool:
        return self._fired

    def before_modeled_allocation(self, label: str) -> int:
        if not isinstance(label, str) or not label:
            raise CompilerInternalError("modeled allocation label is empty")
        self._ordinal = checked_add(self._ordinal, 1, "modeled allocation ordinal")
        if not self._fired and self._target_ordinal == self._ordinal:
            self._fired = True
            self._target_ordinal = None
            raise MemoryError("injected modeled allocation failure: " + label)
        return self._ordinal


class RetainedArena:
    """Peak-concurrent logical X ledger; it does not claim an RSS bound."""

    def __init__(self, maximum: int = 33_554_432) -> None:
        checked_nonnegative_u64(maximum, "RetainedArena.maximum")
        self.maximum = maximum
        self.used = 0
        self.high_water = 0
        self.last_candidate = 0

    def candidate(self, amount: int) -> int:
        checked_nonnegative_u64(amount, "RetainedArena reservation")
        return checked_add(self.used, amount, "RetainedArena reservation")

    def reserve(self, amount: int) -> bool:
        """Reserve logically; false is the stage-46 first-exceed signal."""
        candidate = self.candidate(amount)
        self.last_candidate = candidate
        if candidate > self.maximum:
            self.high_water = max(self.high_water, candidate)
            return False
        self.used = candidate
        self.high_water = max(self.high_water, self.used)
        return True

    def release(self, amount: int) -> None:
        checked_nonnegative_u64(amount, "RetainedArena release")
        if amount > self.used:
            raise CompilerInternalError("arena release exceeds retained charge")
        self.used -= amount

    def modeled_allocate(
        self,
        amount: int,
        label: str,
        allocator: Callable[[], _T],
        fault_injector: Optional[_FaultInjector] = None,
    ) -> Optional[_T]:
        """Guard X, inject/allocate physically, then commit the logical charge.

        ``None`` means the logical candidate exceeded X.  An injected or native
        ``MemoryError`` leaves ``used`` at its exact pre-attempt value.
        """
        candidate = self.candidate(amount)
        self.last_candidate = candidate
        if candidate > self.maximum:
            self.high_water = max(self.high_water, candidate)
            return None
        if fault_injector is not None:
            fault_injector.before_modeled_allocation(label)
        value = allocator()
        self.used = candidate
        self.high_water = max(self.high_water, self.used)
        return value

    def retain_s8_hash_state(
        self, fault_injector: Optional[_FaultInjector] = None
    ) -> Optional[object]:
        """Model one S8 state; caller releases 256 before the next buffer."""
        return self.modeled_allocate(
            SHA256_STATE_CHARGE, "s8-sha256-state", object, fault_injector
        )

    def visit_case_id(
        self,
        case_id: str,
        interned: set,
        fault_injector: Optional[_FaultInjector] = None,
    ) -> Tuple[bool, bool]:
        """Apply the global intern hit/miss rule.

        Returns ``(admitted, created)``.  A hit is ``(True, False)`` and has no
        reservation or modeled ordinal; an X breach is ``(False, False)``.
        """
        encoded = case_id.encode("utf-8")
        if encoded in interned:
            return True, False
        charge = intern_entry_charge(encoded)
        marker = self.modeled_allocate(
            charge, "derived-case-id-intern-miss", object, fault_injector
        )
        if marker is None:
            return False, False
        interned.add(encoded)
        return True, True


class PhaseBScratch:
    """The one physical W_B slab and fixed-offset, allocation-free access."""

    def __init__(self, slab: bytearray) -> None:
        if type(slab) is not bytearray or len(slab) != W_B:
            raise CompilerInternalError("invalid Phase B scratch slab")
        self._slab = slab
        self.scratch_used = 0

    @classmethod
    def allocate(
        cls,
        arena: RetainedArena,
        fault_injector: Optional[_FaultInjector] = None,
    ) -> Optional["PhaseBScratch"]:
        slab = arena.modeled_allocate(
            W_B, "phase-b-scratch-slab", lambda: bytearray(W_B), fault_injector
        )
        return None if slab is None else cls(slab)

    def carve(self, byte_count: int) -> None:
        checked_nonnegative_u64(byte_count, "Phase B scratch carve")
        candidate = checked_add(self.scratch_used, byte_count, "Phase B scratch guard")
        if candidate > W_B:
            raise CompilerInternalError("Phase B scratch guard exceeded W_B")
        self.scratch_used = candidate

    def release_region(self) -> None:
        self.scratch_used = 0

    def clear_counter_block(self) -> None:
        self.release_region()
        self.carve(SWEEP_COUNTER_COUNT * 8)
        for index in range(W_B):
            self._slab[index] = 0

    def clear_mapping_table(self, owner_count: int) -> None:
        if owner_count not in (JUDGE_MAPPING_COUNT, GATE_MAPPING_COUNT):
            raise CompilerInternalError("invalid stage-40 owner count")
        self.release_region()
        byte_count = owner_count * 8
        self.carve(byte_count)
        for index in range(byte_count):
            self._slab[index] = 0

    def get_u64(self, index: int) -> int:
        self._check_index(index)
        return struct.unpack_from(">Q", self._slab, index * 8)[0]

    def set_u64(self, index: int, value: int) -> None:
        self._check_index(index)
        checked_nonnegative_u64(value, "Phase B scratch value")
        struct.pack_into(">Q", self._slab, index * 8, value)

    def _check_index(self, index: int) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise CompilerInternalError("invalid Phase B scratch index")
        if (index + 1) * 8 > self.scratch_used:
            raise CompilerInternalError("Phase B scratch index outside live region")


class SharedHashBuffer:
    """One physical h_max bytearray with disjoint K1/K4 logical reservations."""

    def __init__(self, buffer: bytearray) -> None:
        if type(buffer) is not bytearray or len(buffer) != HASH_BUFFER_CHARGE:
            raise CompilerInternalError("invalid shared hash buffer")
        self.buffer = buffer
        self._logically_live = False

    @classmethod
    def allocate_phase_a(
        cls,
        arena: RetainedArena,
        fault_injector: Optional[_FaultInjector] = None,
    ) -> Optional["SharedHashBuffer"]:
        value = arena.modeled_allocate(
            HASH_BUFFER_CHARGE,
            "shared-hash-buffer",
            lambda: bytearray(HASH_BUFFER_CHARGE),
            fault_injector,
        )
        if value is None:
            return None
        result = cls(value)
        result._logically_live = True
        return result

    def logical_release(self, arena: RetainedArena) -> None:
        if not self._logically_live:
            raise CompilerInternalError("shared hash buffer is not logically live")
        arena.release(HASH_BUFFER_CHARGE)
        self._logically_live = False

    def logical_reserve_phase_d(self, arena: RetainedArena) -> bool:
        if self._logically_live:
            raise CompilerInternalError("shared hash buffer is already logically live")
        admitted = arena.reserve(HASH_BUFFER_CHARGE)
        self._logically_live = admitted
        return admitted


class OutputSink:
    """One exact-size, non-growing output builder with q-sized view writes."""

    def __init__(
        self,
        candidate_size: int,
        limits: Optional[CompileLimits] = None,
        fault_injector: Optional[_FaultInjector] = None,
    ) -> None:
        limits = CompileLimits.frozen() if limits is None else limits
        checked_nonnegative_u64(candidate_size, "OutputSink candidate size")
        platform_size(candidate_size, "OutputSink candidate size")
        if candidate_size > limits.coverage_plan_or_failure_max:
            raise CompilerInternalError("output-builder allocation preceded stage-47 guard")
        if fault_injector is not None:
            fault_injector.before_modeled_allocation("output-builder")
        self._buffer = bytearray(candidate_size)
        self._chunk_max = limits.output_streaming_chunk_max
        self._offset = 0

    @property
    def byte_count(self) -> int:
        return len(self._buffer)

    @property
    def written(self) -> int:
        return self._offset

    def write(self, chunk: Any) -> None:
        try:
            view = memoryview(chunk)
        except TypeError:
            raise CompilerInternalError("output chunk does not export bytes") from None
        try:
            if view.nbytes > self._chunk_max:
                raise CompilerInternalError("output chunk exceeds q_max")
            end = checked_add(self._offset, view.nbytes, "OutputSink write")
            if end > len(self._buffer):
                raise CompilerInternalError("output write exceeds exact candidate size")
            self._buffer[self._offset:end] = view.cast("B")
            self._offset = end
        finally:
            view.release()

    def finish(self) -> bytes:
        if self._offset != len(self._buffer):
            raise CompilerInternalError("output builder is incomplete")
        return bytes(self._buffer)
