"""Contract tests for the deterministic CoverageCompiler fixture generator."""

import hashlib
import json
import re
import unittest
from collections import Counter

from turnvector_benchmark.authority.authority_snapshot import (
    AuthoritySnapshot,
    authority_snapshot_value,
    parse_authority_snapshot,
)
from turnvector_benchmark.authority.bound_bytes import BoundBytesRef
from turnvector_benchmark.authority.compiler_inputs import compute_input_set_sha256
from turnvector_benchmark.authority.traceability_ledger import (
    TraceabilityLedger,
    parse_traceability_ledger,
    traceability_ledger_value,
)

from tests.fixtures.compiler.fixture_utils import (
    EXPECTATION_PATH,
    FIRST_CASE_ID,
    HEX64,
    IDENTIFIER,
    LANE_CASE_COUNTS,
    LANE_GATE_COUNTS,
    LANE_OBLIGATION_COUNTS,
    build_fixture,
    case_ids,
    compact,
    digest,
    jsonl,
    pretty,
    strings,
)
from tests.fixtures.compiler.test_permit_issuer import (
    issue_test_compile_permit,
    payload_fields,
)


EXPECTED_LENGTHS = {
    34: 12, 35: 6, 37: 36, 41: 67, 42: 12, 43: 20, 45: 16,
    47: 15, 48: 119, 50: 6, 53: 24, 54: 12, 55: 80,
}


class CompilerFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = build_fixture()

    def test_real_expectation_digest_and_case_plan_basis(self):
        expectation = json.loads(EXPECTATION_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(EXPECTATION_PATH.read_bytes()).hexdigest(),
            "e312793909965241555cc128ce9a9bca86a052cfc7ca6b005de3a5b81c185453",
        )
        generated = case_ids(expectation)
        self.assertEqual(generated, self.base.generated_case_ids)
        self.assertEqual(len(generated), 425)
        self.assertEqual(generated[0], FIRST_CASE_ID)
        self.assertEqual(Counter(map(len, generated)), Counter(EXPECTED_LENGTHS))
        self.assertEqual(sum(16 + len(value) for value in generated), 26_618)

    def test_catalog_has_exact_required_behavior_bijection(self):
        body = self.base.catalog[1:]
        expectation_pairs = [
            (lane["id"], case["id"])
            for lane in self.base.expectation["lanes"]
            for case in lane["cases"]
        ]
        actual_pairs = [(record["lane_id"], record["behavior_case_id"]) for record in body]
        self.assertEqual(actual_pairs, expectation_pairs)
        self.assertEqual(len(body), 46)
        self.assertEqual(sum(record["required"] for record in body), 46)
        self.assertNotIn("synthetic-case-0047", {record["behavior_case_id"] for record in body})
        counts = Counter(record["lane_id"] for record in body)
        self.assertEqual(list(counts.values()), LANE_OBLIGATION_COUNTS)
        self.assertEqual(self.base.catalog[0]["record_count"], 47)

    def test_source_ranges_and_digests_recompute_from_checked_in_files(self):
        source_by_path = {record["path"]: record for record in self.base.snapshot["source_files"]}
        for catalog_record in self.base.catalog[1:]:
            source = source_by_path[catalog_record["source_path"]]
            raw = (self.base.snapshot["section_records"])
            section = next(
                item for item in raw
                if (item["path"], item["start"], item["end"])
                == (catalog_record["source_path"], catalog_record["section_start"], catalog_record["section_end"])
            )
            disk = (
                __import__("pathlib").Path(__file__).resolve().parent
                / "fixtures" / "compiler" / catalog_record["source_path"]
            ).read_bytes()
            selected = disk[section["start"]:section["end"]]
            self.assertEqual(digest(disk), source["sha256"])
            self.assertEqual(digest(selected), section["sha256"])
            self.assertEqual(section["sha256"], catalog_record["section_sha256"])

    def test_canonical_input_byte_families(self):
        self.assertEqual(self.base.snapshot_bytes, compact(self.base.snapshot))
        self.assertEqual(self.base.reconciliation_bytes, pretty(self.base.reconciliation))
        self.assertEqual(self.base.catalog_bytes, jsonl(self.base.catalog))
        self.assertEqual(self.base.ledger_bytes, pretty(self.base.ledger))
        self.assertTrue(self.base.expectation_bytes.endswith(b"\n"))
        for raw in self.base.input_buffers:
            self.assertNotIn(b"\r", raw)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))

    def test_ledger_structural_parser_builds_immutable_typed_records(self):
        value = traceability_ledger_value(parse_traceability_ledger(
            TraceabilityLedger(BoundBytesRef(self.base.ledger_bytes))
        ))
        self.assertEqual(value.cardinalities.lane_case_counts, tuple(LANE_CASE_COUNTS))
        self.assertEqual(value.cardinalities.lane_gate_counts, tuple(LANE_GATE_COUNTS))
        self.assertEqual(len(value.entities.judge_negative_tests), 46)
        self.assertEqual(len(value.entities.aggregate_negative_tests), 58)
        self.assertEqual(len(value.entities.plumbing_negative_tests), 58)
        self.assertEqual(value.binds.expectation_sha256, digest(self.base.expectation_bytes))

    def test_snapshot_structural_parser_preserves_descriptor_constants(self):
        value = authority_snapshot_value(parse_authority_snapshot(
            AuthoritySnapshot(
                BoundBytesRef(self.base.snapshot_bytes),
                BoundBytesRef(self.base.reconciliation_bytes),
            )
        ))
        descriptor = value.repository_control
        self.assertEqual(descriptor.schema_version, "turnvector.benchmark.repository-control.v1")
        self.assertEqual(descriptor.strict_parser_version, "canonical-strict-parser-v1")
        self.assertEqual(len(self.base.snapshot["repository_control"]), 22)
        self.assertEqual(len(value.source_files), 12)
        self.assertEqual(len(value.section_records), 46)
        self.assertEqual(len(value.historical_objects), 14)

    def test_complete_prior_intern_set_is_disjoint_from_case_ids(self):
        documents = [
            self.base.snapshot, self.base.reconciliation, self.base.expectation,
            *self.base.catalog, self.base.ledger,
        ]
        prior = {
            value for document in documents for value in strings(document)
            if IDENTIFIER.fullmatch(value) and not HEX64.fullmatch(value)
        }
        self.assertFalse(prior.intersection(self.base.generated_case_ids))
        self.assertTrue(all("." in case_id and 34 <= len(case_id) <= 55
                            for case_id in self.base.generated_case_ids))
        self.assertTrue(all("." not in value for value in prior if HEX64.fullmatch(value)))

    def test_f46_variant_rebinds_only_expected_authority_identities(self):
        f46 = build_fixture(variant="f46")
        self.assertEqual(f46.generated_case_ids, self.base.generated_case_ids)
        gates = [gate["id"] for lane in f46.expectation["lanes"] for gate in lane["gates"]]
        self.assertEqual(len(gates), 58)
        self.assertEqual(len(set(gates)), 58)
        self.assertTrue(all(len(gate.encode("ascii")) == 1024 for gate in gates))
        self.assertEqual(
            f46.ledger["binds"]["expectation_sha256"], digest(f46.expectation_bytes)
        )
        self.assertEqual(f46.catalog[0]["expectation_sha256"], digest(f46.expectation_bytes))
        self.assertEqual(f46.ledger["cardinalities"]["obligation_gate_pair_count"], 225)
        self.assertEqual(f46.ledger["cardinalities"]["enforcement_path_count"], 8_899)
        self.assertEqual(f46.ledger["cardinalities"]["path_evidence_record_count"], 41_883)
        self.assertEqual([len(lane["gates"]) for lane in f46.expectation["lanes"]], LANE_GATE_COUNTS)

    def test_c4_has_exactly_one_prior_intern_collision(self):
        fixture = build_fixture(variant="c4")
        self.assertEqual(fixture.ledger["entities"]["judge_ids"].count(FIRST_CASE_ID), 1)
        documents = [fixture.snapshot, fixture.reconciliation, fixture.expectation,
                     *fixture.catalog, fixture.ledger]
        prior = {value for document in documents for value in strings(document)
                 if IDENTIFIER.fullmatch(value) and not HEX64.fullmatch(value)}
        self.assertEqual(prior.intersection(fixture.generated_case_ids), {FIRST_CASE_ID})
        self.assertEqual(sum(16 + len(value) for value in fixture.generated_case_ids[1:]), 26_559)

    def test_permit_payload_binds_all_five_actual_lengths_and_digests(self):
        fields = payload_fields(self.base)
        names = ("authority_snapshot", "source_reconciliation", "expectation", "catalog", "traceability")
        for name, raw in zip(names, self.base.input_buffers):
            self.assertEqual(fields[f"{name}_byte_count"], len(raw))
            self.assertEqual(fields[f"{name}_sha256"], digest(raw))
        permit = issue_test_compile_permit(fields)
        self.assertEqual(permit.payload.input_set_sha256, compute_input_set_sha256(permit.payload))
        self.assertEqual(permit.payload.issuance_kind, "test")


if __name__ == "__main__":
    unittest.main()
