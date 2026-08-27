"""Deterministic generator for the synthetic obligation-catalog fixtures.

This script writes the synthetic catalog/source fixtures under
tests/fixtures/obligation-catalog/. Everything it produces is clearly
synthetic and is NOT accepted authority:

- The catalog is never placed at authority/obligation-catalog-v1.jsonl (the
  accepted-catalog path reserved for the later catalog-content gate).
- Source files are synthetic content, not TurnVector authority.
- Digests and identifiers are synthetic placeholders.

The emitted catalog complies with the accepted PR 5 contract amendment
(proposal revision
b068126b65ddaf8ea3f0f8ec9d1ced7409c3f545662864891e937d15cd8654b4): the header
predecessor is null (lineage genesis), claim_class/observable_seam/
evidence_grade/invalidation_rule are nonempty prose, module_ids is nonempty
unique, blocker_ids follows the four-state readiness truth table, and every
obligation design_gate_revision equals the header design_gate_revision.

Regenerate with:
    .venv/bin/python -B tests/fixtures/obligation-catalog/generate_fixture.py

The output is deterministic: fixed source content, fixed obligation
structure, and canonical JSONL encoding (compact lexical-key ASCII JSON plus
one LF per record).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = ROOT / "source"
CATALOG_PATH = ROOT / "synthetic-catalog-v1.jsonl"

SCHEMA_FAMILY = "turnvector.benchmark.obligation-catalog.v1"
CATALOG_PROFILE_ID = "turnvector-implementation-v2"
COMPILE_CUSTODY_LINEAGE_ID = "tvb-qualification-d0-catalog-v1"
T_MAX = 8
REQUIRED_OBLIGATION_COUNT = 46

LANE_IDS_IN_ORDER = [
    "core-event-replay",
    "scheduler-policy",
    "scheduler-performance",
    "request-serving-lifecycle",
    "mlx-native-correctness",
    "bounded-turn-and-ffi",
    "residency-and-memory-governor",
    "cross-model-serving",
    "observability-qualification",
    "persistence-and-recovery",
    "protocol-and-owner-lifecycle",
    "certification-envelopes",
]
# Per-lane required counts in the successor expectation's lane order.
LANE_COUNTS = [4, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4]
assert sum(LANE_COUNTS) == REQUIRED_OBLIGATION_COUNT

SOURCE_FILES = [
    "context.md",
    "adr/0001.md",
    "adr/0018.md",
    "adr/0019.md",
    "adr/0020.md",
    "adr/0029.md",
    "adr/0030.md",
    "adr/0035.md",
    "architecture.md",
    "seam.md",
]

MARKER = "SYNTHETIC FIXTURE SOURCE - NOT ACCEPTED TURNVECTOR AUTHORITY"

# Fixed synthetic digest placeholders; each is a valid lowercase 64-hex SHA-256
# token but binds nothing real.
_DIGESTS = {
    "design_gate": "ab" * 32,
    "source_reconciliation": "cd" * 32,
    "expectation": "ef" * 32,
    "custody_policy": "12" * 32,
    "custody_domain": "34" * 32,
}


def _source_bytes(rel_path: str) -> bytes:
    lines = [
        MARKER,
        "Synthetic benchmark fixture source used only by TurnVectorBenchmark PR 5 tests.",
        "It is not TurnVector authority and must never be treated as accepted content.",
        "",
        "File: " + rel_path,
        "",
    ]
    for index in range(24):
        lines.append("synthetic line %02d for %s" % (index, rel_path))
    return ("\n".join(lines) + "\n").encode("ascii")


def _section_range(content: bytes, index: int) -> tuple:
    length = len(content)
    start = (index * 53) % length
    want = 16 + (index % 64)
    end = min(start + want, length)
    if start >= end:
        start = max(0, length - 8)
        end = length
    return start, end


def _lane_for(index: int) -> str:
    # index is 0-based over the required obligations in order.
    remaining = index
    for lane, count in zip(LANE_IDS_IN_ORDER, LANE_COUNTS):
        if remaining < count:
            return lane
        remaining -= count
    raise AssertionError("index out of range")


def build_obligation(index: int, source_path: str, source_digest: str, start: int, end: int) -> dict:
    status_plan = {
        2: ("adapter_blocked", ["synthetic-blocker-backend-interface-v1"]),
        6: ("environment_blocked", ["synthetic-blocker-host-profile-v1"]),
        16: ("adapter_blocked", ["synthetic-blocker-backend-interface-v1"]),
        22: ("environment_blocked", ["synthetic-blocker-host-profile-v1"]),
        30: ("adapter_blocked", ["synthetic-blocker-backend-interface-v1", "synthetic-blocker-observability-v1"]),
    }
    status, blockers = status_plan.get(index, ("design_ready", []))
    return {
        "behavior_case_id": "synthetic-case-%04d" % (index + 1),
        "blocker_ids": blockers,
        "claim_class": "synthetic-behavior",
        "design_gate_revision": _DIGESTS["design_gate"],
        "evidence_grade": "synthetic-grade",
        "id": "obligation-%04d" % (index + 1),
        "invalidation_rule": "synthetic source section drift invalidates this obligation",
        "kind": "obligation",
        "lane_id": _lane_for(index),
        "module_ids": ["synthetic-module-a", "synthetic-module-b"],
        "observable_seam": "synthetic-observable-%02d" % (index % 4),
        "readiness_status": status,
        "required": True,
        "seam_id": "synthetic-seam-%02d" % (index % 4),
        "section_end": end,
        "section_sha256": hashlib.sha256(
            SOURCE_ROOT.joinpath(source_path).read_bytes()[start:end]
        ).hexdigest(),
        "section_start": start,
        "source_file_sha256": source_digest,
        "source_path": source_path,
    }


def build_optional_obligation(source_path: str, source_digest: str, start: int, end: int) -> dict:
    return {
        "behavior_case_id": "synthetic-case-0047",
        "blocker_ids": [],
        "claim_class": "synthetic-behavior",
        "design_gate_revision": _DIGESTS["design_gate"],
        "evidence_grade": "synthetic-grade",
        "id": "obligation-0047",
        "invalidation_rule": "synthetic source section drift invalidates this obligation",
        "kind": "obligation",
        "lane_id": "scheduler-policy",
        "module_ids": ["synthetic-module-a"],
        "observable_seam": "synthetic-observable-47",
        "readiness_status": "intentionally_out_of_scope",
        "required": False,
        "seam_id": "synthetic-seam-47",
        "section_end": end,
        "section_sha256": hashlib.sha256(
            SOURCE_ROOT.joinpath(source_path).read_bytes()[start:end]
        ).hexdigest(),
        "section_start": start,
        "source_file_sha256": source_digest,
        "source_path": source_path,
    }


def canonical_line(value: dict) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def main() -> None:
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    for rel_path in SOURCE_FILES:
        path = SOURCE_ROOT.joinpath(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_source_bytes(rel_path))
        assert path.read_bytes().endswith(b"\n")

    records = []
    header = {
        "compile_custody_lineage_id": COMPILE_CUSTODY_LINEAGE_ID,
        "compile_custody_policy_sha256": _DIGESTS["custody_policy"],
        "custody_domain_id": "synthetic-custody-domain-v1",
        "custody_domain_sha256": _DIGESTS["custody_domain"],
        "design_gate_revision": _DIGESTS["design_gate"],
        "expectation_sha256": _DIGESTS["expectation"],
        "id": "synthetic-obligation-catalog-v1",
        "kind": "catalog",
        "lineage_id": "synthetic-obligation-catalog-lineage-v1",
        "predecessor": None,
        "profile_id": CATALOG_PROFILE_ID,
        "record_count": 1 + REQUIRED_OBLIGATION_COUNT + 1,
        "required_obligation_count": REQUIRED_OBLIGATION_COUNT,
        "schema_version": SCHEMA_FAMILY,
        "source_reconciliation_sha256": _DIGESTS["source_reconciliation"],
        "t_max": T_MAX,
    }
    records.append(canonical_line(header))

    obligations = []
    for index in range(REQUIRED_OBLIGATION_COUNT):
        source_path = SOURCE_FILES[index % len(SOURCE_FILES)]
        content = SOURCE_ROOT.joinpath(source_path).read_bytes()
        start, end = _section_range(content, index)
        obligations.append(
            build_obligation(
                index,
                source_path,
                hashlib.sha256(content).hexdigest(),
                start,
                end,
            )
        )
    optional_source = SOURCE_FILES[0]
    optional_content = SOURCE_ROOT.joinpath(optional_source).read_bytes()
    obligations.append(
        build_optional_obligation(
            optional_source,
            hashlib.sha256(optional_content).hexdigest(),
            0,
            16,
        )
    )
    for obligation in sorted(obligations, key=lambda item: item["id"]):
        records.append(canonical_line(obligation))

    CATALOG_PATH.write_bytes(b"".join(records))
    print("wrote %s (%d records, %d bytes)" % (CATALOG_PATH, len(records), CATALOG_PATH.stat().st_size))
    print("sha256=%s" % hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest())


if __name__ == "__main__":
    sys.exit(main())
