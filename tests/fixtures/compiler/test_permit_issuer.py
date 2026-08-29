"""Tests-only CompilePermit issuer bound to compiler fixture bytes."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, Mapping

from turnvector_benchmark.authority.compile_permit import (
    PermitPayload,
    _issue_test_compile_permit,
    _test_issuer_token,
)
from turnvector_benchmark.authority.compiler_inputs import compute_input_set_sha256
from turnvector_benchmark.compile_limits import CompileLimits

from .fixture_utils import CompilerFixture, CUSTODY_SHA256, POLICY_SHA256, digest, synthetic_digest


def compile_limits_sha256(limits: CompileLimits | None = None) -> str:
    limits = limits or CompileLimits.frozen()
    value = {
        "limits": dataclasses.asdict(limits),
        "schema_version": "turnvector.benchmark.compile-limits.v1",
    }
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def payload_fields(fixture: CompilerFixture, **overrides: Any) -> dict[str, Any]:
    snapshot, reconciliation, expectation, catalog, ledger = fixture.input_buffers
    fields = {
        "issuance_kind": "test",
        "custody_domain_id": "fixture-custody-domain-v1",
        "custody_domain_sha256": CUSTODY_SHA256,
        "custody_lineage_id": "tvb-qualification-d0-catalog-v1",
        "attempt": 1,
        "t_max": 8,
        "start_event_sha256": synthetic_digest("start-event"),
        "chronology_prefix_sha256": synthetic_digest("chronology-prefix"),
        "chronology_prefix_byte_count": 128,
        "compiler_build_sha256": synthetic_digest("compiler-build"),
        "execution_closure_sha256": synthetic_digest("execution-closure"),
        "compile_custody_policy_sha256": POLICY_SHA256,
        "authority_snapshot_sha256": digest(snapshot),
        "authority_snapshot_byte_count": len(snapshot),
        "source_reconciliation_sha256": digest(reconciliation),
        "source_reconciliation_byte_count": len(reconciliation),
        "expectation_sha256": digest(expectation),
        "expectation_byte_count": len(expectation),
        "catalog_sha256": digest(catalog),
        "catalog_byte_count": len(catalog),
        "traceability_sha256": digest(ledger),
        "traceability_byte_count": len(ledger),
        "compile_limits_sha256": compile_limits_sha256(),
        "input_set_sha256": "0" * 64,
    }
    fields.update(overrides)
    provisional = PermitPayload(**fields)
    fields["input_set_sha256"] = compute_input_set_sha256(provisional)
    if "input_set_sha256" in overrides:
        fields["input_set_sha256"] = overrides["input_set_sha256"]
    return fields


def issue_test_compile_permit(
    fixture_or_fields: CompilerFixture | Mapping[str, Any], **overrides: Any
):
    if isinstance(fixture_or_fields, CompilerFixture):
        fields = payload_fields(fixture_or_fields, **overrides)
    else:
        fields = dict(fixture_or_fields)
        fields.update(overrides)
    payload = PermitPayload(**fields)
    return _issue_test_compile_permit(payload, _test_issuer_token())
