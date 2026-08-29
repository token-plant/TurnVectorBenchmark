"""Process-bound, single-use capability for the private CoverageCompiler."""

from __future__ import annotations

import copy
import inspect
import os
import re
import threading
from dataclasses import dataclass, replace
from typing import Any, Final

from turnvector_benchmark.compile_limits import checked_nonnegative_u64
from turnvector_benchmark.core import ContractError, IDENTIFIER_RE

from .errors import CompilerInternalError, CompilerPreconditionViolation

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_T_MAX: Final = 8

# The complete entry-token set.  Both identities are created exactly once at
# import time and all authorization comparisons below use ``is``.
_PRODUCTION_ENTRY_TOKEN: Final = object()
_TEST_ISSUER_TOKEN: Final = object()


def _input_identity_mismatch() -> CompilerPreconditionViolation:
    return CompilerPreconditionViolation("input_identity_mismatch")


def _require_identifier(value: Any) -> None:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise _input_identity_mismatch()


def _require_digest(value: Any) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _input_identity_mismatch()


def _require_u64(value: Any) -> None:
    try:
        checked_nonnegative_u64(value, "PermitPayload byte count")
    except ContractError:
        raise _input_identity_mismatch() from None


@dataclass(frozen=True)
class PermitPayload:
    """The exact frozen 24-field identity captured by a compile permit."""

    issuance_kind: str
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
    authority_snapshot_sha256: str
    authority_snapshot_byte_count: int
    source_reconciliation_sha256: str
    source_reconciliation_byte_count: int
    expectation_sha256: str
    expectation_byte_count: int
    catalog_sha256: str
    catalog_byte_count: int
    traceability_sha256: str
    traceability_byte_count: int
    compile_limits_sha256: str
    input_set_sha256: str

    def __post_init__(self) -> None:
        if self.issuance_kind not in ("production", "test"):
            raise _input_identity_mismatch()
        _require_identifier(self.custody_domain_id)
        _require_identifier(self.custody_lineage_id)
        for name in (
            "custody_domain_sha256",
            "start_event_sha256",
            "chronology_prefix_sha256",
            "compiler_build_sha256",
            "execution_closure_sha256",
            "compile_custody_policy_sha256",
            "authority_snapshot_sha256",
            "source_reconciliation_sha256",
            "expectation_sha256",
            "catalog_sha256",
            "traceability_sha256",
            "compile_limits_sha256",
            "input_set_sha256",
        ):
            _require_digest(getattr(self, name))
        for name in (
            "chronology_prefix_byte_count",
            "authority_snapshot_byte_count",
            "source_reconciliation_byte_count",
            "expectation_byte_count",
            "catalog_byte_count",
            "traceability_byte_count",
        ):
            _require_u64(getattr(self, name))
        try:
            checked_nonnegative_u64(self.attempt, "PermitPayload.attempt")
            checked_nonnegative_u64(self.t_max, "PermitPayload.t_max")
        except ContractError:
            raise _input_identity_mismatch() from None
        if self.t_max != _T_MAX or self.attempt < 1 or self.attempt > self.t_max:
            raise _input_identity_mismatch()


class CompilePermit:
    """Unexported mutable capability consumed atomically exactly once."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise CompilerInternalError("CompilePermit cannot be subclassed")

    def __init__(self, payload: PermitPayload, entry_token: object) -> None:
        caller = inspect.currentframe()
        try:
            caller_name = caller.f_back.f_code.co_name if caller and caller.f_back else ""
        finally:
            del caller
        if caller_name not in ("_issue_compile_permit", "_issue_test_compile_permit"):
            raise CompilerInternalError("CompilePermit construction is private")
        if entry_token is not _PRODUCTION_ENTRY_TOKEN and entry_token is not _TEST_ISSUER_TOKEN:
            raise CompilerInternalError("invalid CompilePermit entry token")
        if not isinstance(payload, PermitPayload):
            raise CompilerInternalError("invalid CompilePermit payload")
        self._payload = payload
        self._issuing_pid = os.getpid()
        self._entry_token = entry_token
        self._consumed = False
        self._consume_lock = threading.Lock()

    @property
    def payload(self) -> PermitPayload:
        return self._payload

    def consume(self) -> PermitPayload:
        """Atomically consume the permit and return its payload exactly once."""
        with self._consume_lock:
            if self._consumed:
                raise CompilerPreconditionViolation("permit_reuse")
            self._consumed = True
            return self._payload

    def _check_issuing_process(self) -> None:
        if self._issuing_pid != os.getpid():
            raise CompilerInternalError("CompilePermit crossed a process boundary")

    def _check_production_entry(self) -> None:
        if self._entry_token is not _PRODUCTION_ENTRY_TOKEN:
            raise CompilerPreconditionViolation("compiler_identity_mismatch")

    def _check_test_entry(self) -> None:
        if self._entry_token is not _TEST_ISSUER_TOKEN:
            raise CompilerInternalError("production permit reached the test compiler entry")

    def __copy__(self) -> "CompilePermit":
        raise CompilerInternalError("CompilePermit cannot be copied")

    def __deepcopy__(self, memo: Any) -> "CompilePermit":
        raise CompilerInternalError("CompilePermit cannot be deep-copied")

    def __reduce__(self) -> Any:
        raise CompilerInternalError("CompilePermit cannot be pickled")

    def __reduce_ex__(self, protocol: int) -> Any:
        raise CompilerInternalError("CompilePermit cannot be pickled")


def _caller_module_name() -> str:
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is None:
            raise CompilerInternalError("caller frame is unavailable")
        name = caller.f_globals.get("__name__")
        if not isinstance(name, str):
            raise CompilerInternalError("caller module identity is unavailable")
        return name
    finally:
        del frame


def _production_entry_token() -> object:
    if _caller_module_name() != "turnvector_benchmark.compile_custody":
        raise CompilerInternalError("production token requested outside CompileCustody")
    return _PRODUCTION_ENTRY_TOKEN


def _test_issuer_token() -> object:
    if not _caller_module_name().startswith("tests."):
        raise CompilerInternalError("test token requested outside tests")
    return _TEST_ISSUER_TOKEN


def _issue_compile_permit(payload: PermitPayload, token: object) -> CompilePermit:
    if token is not _PRODUCTION_ENTRY_TOKEN:
        raise CompilerInternalError("invalid production issuance token")
    if not isinstance(payload, PermitPayload):
        raise CompilerInternalError("invalid PermitPayload")
    return CompilePermit(replace(payload, issuance_kind="production"), token)


def _issue_test_compile_permit(payload: PermitPayload, token: object) -> CompilePermit:
    if token is not _TEST_ISSUER_TOKEN:
        raise CompilerInternalError("invalid test issuance token")
    if not isinstance(payload, PermitPayload):
        raise CompilerInternalError("invalid PermitPayload")
    # The tests-only issuer supplies the synthetic custody identities.  This
    # factory fixes their provenance kind without inventing alternate values.
    return CompilePermit(replace(payload, issuance_kind="test"), token)


# Keep accidental module imports honest: copy is imported only so the standard
# copy protocol routes through the explicit methods above.
assert copy.copy is not None
