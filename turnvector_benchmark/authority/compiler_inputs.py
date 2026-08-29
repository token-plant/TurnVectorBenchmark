"""Exact five-buffer assembly, ordering, hashing, and input-set identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final, Iterator, Tuple

from .bound_bytes import BoundBytesRef
from .errors import CompilerInternalError, CompilerPreconditionViolation

BUFFER_ORDER: Final = (
    "authority_snapshot",
    "source_reconciliation",
    "benchmark_expectation",
    "obligation_catalog",
    "traceability_ledger",
)


@dataclass(frozen=True)
class CompilerInputs:
    """The exact five serialized buffers B in their frozen traversal order."""

    authority_snapshot_ref: BoundBytesRef
    source_reconciliation_ref: BoundBytesRef
    expectation_ref: BoundBytesRef
    catalog_ref: BoundBytesRef
    traceability_ref: BoundBytesRef

    def __post_init__(self) -> None:
        for ref in self.refs():
            if not isinstance(ref, BoundBytesRef):
                raise CompilerPreconditionViolation("input_identity_mismatch")

    @classmethod
    def from_envelopes(
        cls,
        AuthoritySnapshot: Any,
        ObligationCatalog: Any,
        BenchmarkExpectation: Any,
        TraceabilityLedger: Any,
    ) -> "CompilerInputs":
        """Assemble B from the four exact nominal compiler arguments."""
        try:
            return cls(
                AuthoritySnapshot.snapshot_ref,
                AuthoritySnapshot.source_reconciliation_ref,
                BenchmarkExpectation.expectation_ref,
                ObligationCatalog.catalog_ref,
                TraceabilityLedger.ledger_ref,
            )
        except AttributeError:
            raise CompilerInternalError("invalid nominal compiler input envelope") from None

    def refs(self) -> Tuple[BoundBytesRef, ...]:
        return (
            self.authority_snapshot_ref,
            self.source_reconciliation_ref,
            self.expectation_ref,
            self.catalog_ref,
            self.traceability_ref,
        )

    def iter_named(self) -> Iterator[Tuple[str, BoundBytesRef]]:
        return iter(zip(BUFFER_ORDER, self.refs()))

    def nbytes(self) -> Tuple[int, int, int, int, int]:
        return tuple(ref.nbytes for ref in self.refs())  # type: ignore[return-value]


def sha256_bound_bytes(ref: BoundBytesRef) -> str:
    """Hash one admitted buffer without making a payload copy."""
    if not isinstance(ref, BoundBytesRef):
        raise CompilerInternalError("hash input is not BoundBytesRef")
    state = hashlib.sha256()
    state.update(ref.buffer)
    return state.hexdigest()


def observed_input_digests(inputs: CompilerInputs) -> Tuple[str, str, str, str, str]:
    """Hash the five buffers one at a time in the frozen S8 order."""
    return tuple(sha256_bound_bytes(ref) for ref in inputs.refs())  # type: ignore[return-value]


def input_set_preimage(payload: Any) -> bytes:
    """Return the exact 15-field compact lexical JSON+LF preimage."""
    names = (
        "attempt",
        "authority_snapshot_byte_count",
        "authority_snapshot_sha256",
        "catalog_byte_count",
        "catalog_sha256",
        "compile_limits_sha256",
        "custody_domain_id",
        "custody_domain_sha256",
        "custody_lineage_id",
        "expectation_byte_count",
        "expectation_sha256",
        "source_reconciliation_byte_count",
        "source_reconciliation_sha256",
        "traceability_byte_count",
        "traceability_sha256",
    )
    try:
        value = {name: getattr(payload, name) for name in names}
    except AttributeError:
        raise CompilerInternalError("input-set payload lacks a frozen field") from None
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii") + b"\n"
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise CompilerInternalError("input-set preimage is not canonically encodable") from error
    return encoded


def compute_input_set_sha256(payload: Any) -> str:
    """Compute the exact input-set digest bound by PermitPayload."""
    return hashlib.sha256(input_set_preimage(payload)).hexdigest()
