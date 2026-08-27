"""Shared synthetic-fixture helpers for obligation-catalog tests."""

import json
import tempfile
from pathlib import Path

from turnvector_benchmark.canonical import canonical_jsonl_line
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.obligation_catalog import load_obligation_catalog

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "obligation-catalog"
CATALOG = FIXTURE_DIR / "synthetic-catalog-v1.jsonl"
SOURCE_ROOT = FIXTURE_DIR / "source"
SYNTHETIC_CATALOG_SHA256 = "20db49537d8032f321bae99430458d1ad5b1a9ccb2295e45b63a8d2c97269fac"


def fixture_objects():
    text = CATALOG.read_text(encoding="ascii")
    return [json.loads(line) for line in text.splitlines() if line]


def optional_record(index):
    return {
        "behavior_case_id": "synthetic-case-%04d" % index,
        "blocker_ids": [],
        "claim_class": "synthetic-behavior",
        "design_gate_revision": "ab" * 32,
        "evidence_grade": "synthetic-grade",
        "id": "obligation-%04d" % index,
        "invalidation_rule": "synthetic source section drift invalidates this obligation",
        "kind": "obligation",
        "lane_id": "scheduler-policy",
        "module_ids": ["synthetic-module-a"],
        "observable_seam": "synthetic-observable-opt",
        "readiness_status": "intentionally_out_of_scope",
        "required": False,
        "seam_id": "synthetic-seam-opt",
        "section_end": 16,
        "section_sha256": "0" * 64,
        "section_start": 0,
        "source_file_sha256": "0" * 64,
        "source_path": "context.md",
    }


def write_variant(objects, directory):
    path = Path(directory) / "catalog.jsonl"
    path.write_bytes(b"".join(canonical_jsonl_line(obj) for obj in objects))
    return path


def raw_variant(objects):
    """Serialize *objects* with plain json.dumps into raw JSONL bytes.

    Bypasses the canonical serializer's input validation so tests can
    manufacture raw invalid bytes (floats, noncanonical escapes) that the
    loader's strict parser must reject.
    """
    lines = [
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        for obj in objects
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def load_variant(mutator):
    objects = fixture_objects()
    mutator(objects)
    with tempfile.TemporaryDirectory() as directory:
        return load_obligation_catalog(write_variant(objects, directory))


def expect_contract_error(mutator, message=None):
    try:
        load_variant(mutator)
    except ContractError as error:
        if message is not None and message not in str(error):
            raise AssertionError(
                f"expected message containing {message!r}, got {error!r}"
            ) from error
        return
    raise AssertionError("expected ContractError")


def expect_raw_contract_error(raw):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalog.jsonl"
        path.write_bytes(raw)
        try:
            load_obligation_catalog(path)
        except ContractError:
            return
    raise AssertionError("expected ContractError")
