"""Strict canonical JSONL loader for the obligation-catalog contract.

Schema family: ``turnvector.benchmark.obligation-catalog.v1``. The catalog is
canonical JSONL (see :mod:`turnvector_benchmark.canonical`): one compact JSON
object per line, recursively lexical keys, ASCII escaping, LF after every line
including the last, header record first, and the remaining obligation records
sorted by stable id.

Contract decisions implemented here are the accepted PR 5 contract
amendment (proposal revision
b068126b65ddaf8ea3f0f8ec9d1ced7409c3f545662864891e937d15cd8654b4, review
round TVB-AX-DETAIL-DESIGN-20260826-PR5A-R1-20260827T081347Z), applied on top
of docs/D0-AUTHORITY-DESIGN.md (accepted design revision
3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a,
"Obligation Catalog contract"):

- Header exact fields and frozen constants: ``profile_id`` =
  ``turnvector-implementation-v2``, ``compile_custody_lineage_id`` =
  ``tvb-qualification-d0-catalog-v1``, ``t_max`` = 8, and
  ``required_obligation_count`` = 46. ``record_count`` is the total number of
  JSONL records including the header (the accepted catalog will therefore
  declare 47), and ``required_obligation_count`` is the count of
  ``required=true`` records; the loader enforces both equal their actual
  values.
- ``predecessor`` is ``null`` for a lineage-genesis header and otherwise one
  strict object with exactly ``compile_custody_lineage_id`` (identifier) and
  ``chronology_sha256`` (lowercase SHA-256), naming the predecessor
  CompileCustody lineage q and the finalized FinalHistoryView
  ``chronology_sha256`` field chi. Unknown/missing fields and a predecessor q
  equal to the current header ``compile_custody_lineage_id`` are rejected.
- ``custody_domain_id``/``custody_domain_sha256``, ``lineage_id``, and
  ``predecessor`` are preserved verbatim as binding data; mutating them
  changes the catalog file digest.
- Field domains: ``id``, ``*_id``, and ``*_ids`` elements use the stable
  identifier grammar. ``claim_class``, ``observable_seam``,
  ``evidence_grade``, and ``invalidation_rule`` are nonempty Unicode strings
  (prose) with no per-field byte cap; the whole catalog stays inside
  ``largest_single_serialized_parser_input_max`` bytes. ``module_ids`` has at
  least one identifier and no duplicates; ``blocker_ids`` has no duplicates
  and may be empty only in the readiness states below.
- Readiness/blocker algebra is the exact four-state truth table: a required
  record uses ``design_ready`` (empty blockers), ``adapter_blocked``
  (nonempty blockers), or ``environment_blocked`` (nonempty blockers);
  ``intentionally_out_of_scope`` is allowed only when ``required=false`` with
  empty blockers and is outside O_p. Every other combination fails closed.
  Required records never disappear because they are blocked: they count
  toward the frozen 46.
- Per-lane required-obligation counts are exact: 4,3,3,4,4,4,4,4,4,4,4,4 in
  the successor expectation's lane order, summing to 46. ``(lane_id,
  behavior_case_id)`` is unique across the catalog (required and optional),
  and every obligation ``design_gate_revision`` must equal the header
  ``design_gate_revision``.
- The byte-range convention is documented in
  :mod:`turnvector_benchmark.obligation_sources`: half-open 0-based byte
  offsets with ``section_end`` allowed to equal the file length but never
  exceed it. No final obligation bodies exist in this PR; the loader accepts
  only synthetic catalogs and no accepted catalog is placed at
  ``authority/obligation-catalog-v1.jsonl``.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .canonical import (
    parse_canonical_jsonl_records,
    read_no_follow_regular,
    require_boolean,
    require_identifier,
    require_posix_path,
    require_sha256,
    require_strict_keys,
    require_string,
    require_u64,
)
from .compile_limits import CompileLimits
from .core import ContractError

SCHEMA_FAMILY = "turnvector.benchmark.obligation-catalog.v1"
CATALOG_PROFILE_ID = "turnvector-implementation-v2"
COMPILE_CUSTODY_LINEAGE_ID = "tvb-qualification-d0-catalog-v1"
T_MAX = 8
REQUIRED_OBLIGATION_COUNT = 46

CATALOG_KIND = "catalog"
OBLIGATION_KIND = "obligation"

READY_STATUS = "design_ready"
ADAPTER_BLOCKED_STATUS = "adapter_blocked"
ENVIRONMENT_BLOCKED_STATUS = "environment_blocked"
OUT_OF_SCOPE_STATUS = "intentionally_out_of_scope"
REQUIRED_STATUSES = (READY_STATUS, ADAPTER_BLOCKED_STATUS, ENVIRONMENT_BLOCKED_STATUS)
ALL_STATUSES = REQUIRED_STATUSES + (OUT_OF_SCOPE_STATUS,)

# Exact required-obligation counts by lane in the successor expectation's lane
# order (4,3,3,4,4,4,4,4,4,4,4,4), summing to 46.
EXACT_LANE_OBLIGATION_COUNTS = {
    "core-event-replay": 4,
    "scheduler-policy": 3,
    "scheduler-performance": 3,
    "request-serving-lifecycle": 4,
    "mlx-native-correctness": 4,
    "bounded-turn-and-ffi": 4,
    "residency-and-memory-governor": 4,
    "cross-model-serving": 4,
    "observability-qualification": 4,
    "persistence-and-recovery": 4,
    "protocol-and-owner-lifecycle": 4,
    "certification-envelopes": 4,
}

_HEADER_FIELDS = (
    "kind",
    "schema_version",
    "id",
    "profile_id",
    "lineage_id",
    "predecessor",
    "design_gate_revision",
    "source_reconciliation_sha256",
    "expectation_sha256",
    "compile_custody_policy_sha256",
    "custody_domain_id",
    "custody_domain_sha256",
    "compile_custody_lineage_id",
    "t_max",
    "required_obligation_count",
    "record_count",
)

_OBLIGATION_FIELDS = (
    "kind",
    "id",
    "required",
    "claim_class",
    "source_path",
    "source_file_sha256",
    "section_start",
    "section_end",
    "section_sha256",
    "module_ids",
    "seam_id",
    "observable_seam",
    "evidence_grade",
    "invalidation_rule",
    "lane_id",
    "behavior_case_id",
    "readiness_status",
    "blocker_ids",
    "design_gate_revision",
)


@dataclass(frozen=True)
class CatalogPredecessor:
    """Immutable predecessor custody-history binding.

    Names the predecessor CompileCustody lineage q and the SHA-256 field chi
    of its finalized FinalHistoryView. PR 5 validates shape, canonical
    encoding, digest grammar, and current-vs-predecessor q inequality only;
    it cannot prove that ``null`` is historically genesis or that a
    referenced chronology exists and is preserved.
    """

    compile_custody_lineage_id: str
    chronology_sha256: str


@dataclass(frozen=True)
class CatalogHeader:
    kind: str
    schema_version: str
    id: str
    profile_id: str
    lineage_id: str
    predecessor: Optional[CatalogPredecessor]
    design_gate_revision: str
    source_reconciliation_sha256: str
    expectation_sha256: str
    compile_custody_policy_sha256: str
    custody_domain_id: str
    custody_domain_sha256: str
    compile_custody_lineage_id: str
    t_max: int
    required_obligation_count: int
    record_count: int


@dataclass(frozen=True)
class ObligationRecord:
    kind: str
    id: str
    required: bool
    claim_class: str
    source_path: str
    source_file_sha256: str
    section_start: int
    section_end: int
    section_sha256: str
    module_ids: Tuple[str, ...]
    seam_id: str
    observable_seam: str
    evidence_grade: str
    invalidation_rule: str
    lane_id: str
    behavior_case_id: str
    readiness_status: str
    blocker_ids: Tuple[str, ...]
    design_gate_revision: str


@dataclass(frozen=True)
class ObligationCatalog:
    header: CatalogHeader
    obligations: Tuple[ObligationRecord, ...]
    source_path: Path
    file_sha256: str
    file_size: int

    @property
    def required_obligations(self) -> Tuple[ObligationRecord, ...]:
        """The required records forming O_p (exactly 46 for accepted catalogs)."""
        return tuple(record for record in self.obligations if record.required)


_PREDECESSOR_FIELDS = ("compile_custody_lineage_id", "chronology_sha256")


def _parse_predecessor(value: Any, where: str) -> Optional[CatalogPredecessor]:
    """Parse the header predecessor: ``null`` or one strict q/chi object."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContractError(f"{where}.predecessor must be null or an object")
    require_strict_keys(value, _PREDECESSOR_FIELDS, f"{where}.predecessor")
    return CatalogPredecessor(
        compile_custody_lineage_id=require_identifier(
            value["compile_custody_lineage_id"],
            f"{where}.predecessor.compile_custody_lineage_id",
        ),
        chronology_sha256=require_sha256(
            value["chronology_sha256"], f"{where}.predecessor.chronology_sha256"
        ),
    )


def _parse_header(value: Dict[str, Any], where: str) -> CatalogHeader:
    require_strict_keys(value, _HEADER_FIELDS, where)
    if value["kind"] != CATALOG_KIND:
        raise ContractError(f"{where}.kind must equal {CATALOG_KIND!r}")
    schema_version = require_string(value["schema_version"], f"{where}.schema_version")
    if schema_version != SCHEMA_FAMILY:
        raise ContractError(f"{where}.schema_version must equal {SCHEMA_FAMILY!r}")
    header_id = require_identifier(value["id"], f"{where}.id")
    profile_id = require_string(value["profile_id"], f"{where}.profile_id")
    if profile_id != CATALOG_PROFILE_ID:
        raise ContractError(f"{where}.profile_id must equal {CATALOG_PROFILE_ID!r}")
    lineage_id = require_identifier(value["lineage_id"], f"{where}.lineage_id")
    predecessor = _parse_predecessor(value["predecessor"], where)
    compile_custody_lineage_id = require_string(
        value["compile_custody_lineage_id"], f"{where}.compile_custody_lineage_id"
    )
    if compile_custody_lineage_id != COMPILE_CUSTODY_LINEAGE_ID:
        raise ContractError(
            f"{where}.compile_custody_lineage_id must equal {COMPILE_CUSTODY_LINEAGE_ID!r}"
        )
    if (
        predecessor is not None
        and predecessor.compile_custody_lineage_id == compile_custody_lineage_id
    ):
        raise ContractError(
            f"{where}.predecessor.compile_custody_lineage_id must differ from the "
            f"current header compile_custody_lineage_id"
        )
    t_max = require_u64(value["t_max"], f"{where}.t_max")
    if t_max != T_MAX:
        raise ContractError(f"{where}.t_max must equal {T_MAX}")
    required_obligation_count = require_u64(
        value["required_obligation_count"], f"{where}.required_obligation_count"
    )
    if required_obligation_count != REQUIRED_OBLIGATION_COUNT:
        raise ContractError(
            f"{where}.required_obligation_count must equal {REQUIRED_OBLIGATION_COUNT}"
        )
    return CatalogHeader(
        kind=CATALOG_KIND,
        schema_version=schema_version,
        id=header_id,
        profile_id=profile_id,
        lineage_id=lineage_id,
        predecessor=predecessor,
        design_gate_revision=require_sha256(
            value["design_gate_revision"], f"{where}.design_gate_revision"
        ),
        source_reconciliation_sha256=require_sha256(
            value["source_reconciliation_sha256"], f"{where}.source_reconciliation_sha256"
        ),
        expectation_sha256=require_sha256(
            value["expectation_sha256"], f"{where}.expectation_sha256"
        ),
        compile_custody_policy_sha256=require_sha256(
            value["compile_custody_policy_sha256"], f"{where}.compile_custody_policy_sha256"
        ),
        custody_domain_id=require_identifier(
            value["custody_domain_id"], f"{where}.custody_domain_id"
        ),
        custody_domain_sha256=require_sha256(
            value["custody_domain_sha256"], f"{where}.custody_domain_sha256"
        ),
        compile_custody_lineage_id=compile_custody_lineage_id,
        t_max=t_max,
        required_obligation_count=required_obligation_count,
        record_count=require_u64(value["record_count"], f"{where}.record_count"),
    )


def _parse_obligation(value: Dict[str, Any], where: str) -> ObligationRecord:
    require_strict_keys(value, _OBLIGATION_FIELDS, where)
    if value["kind"] != OBLIGATION_KIND:
        raise ContractError(f"{where}.kind must equal {OBLIGATION_KIND!r}")
    record_id = require_identifier(value["id"], f"{where}.id")
    required = require_boolean(value["required"], f"{where}.required")
    lane_id = require_identifier(value["lane_id"], f"{where}.lane_id")
    if lane_id not in EXACT_LANE_OBLIGATION_COUNTS:
        raise ContractError(f"{where}.lane_id is not in the exact successor lane set")
    section_start = require_u64(value["section_start"], f"{where}.section_start")
    section_end = require_u64(value["section_end"], f"{where}.section_end")
    if section_start >= section_end:
        raise ContractError(
            f"{where} section must be a nonempty half-open range with start < end"
        )
    module_ids = _unique_identifiers(value["module_ids"], f"{where}.module_ids", min_items=1)
    blocker_ids = _unique_identifiers(value["blocker_ids"], f"{where}.blocker_ids", min_items=0)
    readiness_status = require_string(value["readiness_status"], f"{where}.readiness_status")
    if readiness_status not in ALL_STATUSES:
        raise ContractError(
            f"{where}.readiness_status must be one of {', '.join(ALL_STATUSES)}"
        )
    _check_readiness_algebra(required, readiness_status, blocker_ids, where)
    return ObligationRecord(
        kind=OBLIGATION_KIND,
        id=record_id,
        required=required,
        claim_class=require_string(value["claim_class"], f"{where}.claim_class"),
        source_path=require_posix_path(value["source_path"], f"{where}.source_path"),
        source_file_sha256=require_sha256(
            value["source_file_sha256"], f"{where}.source_file_sha256"
        ),
        section_start=section_start,
        section_end=section_end,
        section_sha256=require_sha256(value["section_sha256"], f"{where}.section_sha256"),
        module_ids=module_ids,
        seam_id=require_identifier(value["seam_id"], f"{where}.seam_id"),
        observable_seam=require_string(
            value["observable_seam"], f"{where}.observable_seam"
        ),
        evidence_grade=require_string(
            value["evidence_grade"], f"{where}.evidence_grade"
        ),
        invalidation_rule=require_string(
            value["invalidation_rule"], f"{where}.invalidation_rule"
        ),
        lane_id=lane_id,
        behavior_case_id=require_identifier(
            value["behavior_case_id"], f"{where}.behavior_case_id"
        ),
        readiness_status=readiness_status,
        blocker_ids=blocker_ids,
        design_gate_revision=require_sha256(
            value["design_gate_revision"], f"{where}.design_gate_revision"
        ),
    )


def _unique_identifiers(value: Any, where: str, min_items: int) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    if len(value) < min_items:
        raise ContractError(f"{where} must contain at least {min_items} item(s)")
    items = tuple(require_identifier(item, f"{where}[{index}]") for index, item in enumerate(value))
    if len(items) != len(set(items)):
        raise ContractError(f"{where} must not contain duplicates")
    return items


def _check_readiness_algebra(
    required: bool, readiness_status: str, blocker_ids: Tuple[str, ...], where: str
) -> None:
    if required:
        if readiness_status not in REQUIRED_STATUSES:
            raise ContractError(
                f"{where} required records must use design_ready, adapter_blocked, "
                f"or environment_blocked; got {readiness_status!r}"
            )
        if readiness_status == READY_STATUS and blocker_ids:
            raise ContractError(
                f"{where} a design_ready record must not name blockers"
            )
        if readiness_status != READY_STATUS and not blocker_ids:
            raise ContractError(
                f"{where} a blocked record must name at least one blocker"
            )
    else:
        if readiness_status != OUT_OF_SCOPE_STATUS:
            raise ContractError(
                f"{where} intentionally_out_of_scope is the only readiness status "
                f"allowed for required=false records"
            )
        if blocker_ids:
            raise ContractError(
                f"{where} an intentionally_out_of_scope record must not name blockers"
            )

