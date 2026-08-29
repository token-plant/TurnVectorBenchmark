"""Five-input strict schema and precedence-boundary tests."""

import copy
import unittest

from turnvector_benchmark.authority.authority_snapshot import authority_snapshot_value
from turnvector_benchmark.authority.contract_json import (
    InvalidCanonicalJson,
    is_canonical_json,
    is_canonical_jsonl,
    parse_json_object,
    parse_jsonl_records,
)
from turnvector_benchmark.authority.traceability_ledger import traceability_ledger_value
from turnvector_benchmark.core import ContractError

from tests.fixtures.compiler.fixture_utils import build_fixture, compact, pretty


class CompilerSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def test_all_five_fixture_inputs_have_their_frozen_byte_forms(self):
        f = self.fixture
        self.assertTrue(is_canonical_json(f.snapshot_bytes, parse_json_object(f.snapshot_bytes, "snapshot")))
        self.assertTrue(is_canonical_json(f.reconciliation_bytes,
                                          parse_json_object(f.reconciliation_bytes, "reconciliation"), indent=2))
        self.assertTrue(is_canonical_jsonl(f.catalog_bytes,
                                           parse_jsonl_records(f.catalog_bytes, "catalog")))
        self.assertTrue(is_canonical_json(f.ledger_bytes,
                                          parse_json_object(f.ledger_bytes, "ledger"), indent=2))
        # Expectation keeps its accepted declaration-key order rather than a lexical rewrite.
        self.assertFalse(is_canonical_json(f.expectation_bytes,
                                           parse_json_object(f.expectation_bytes, "expectation"), indent=2))

    def test_duplicate_keys_are_retained_in_every_object_input(self):
        replacements = [
            (self.fixture.snapshot_bytes, b'"clean":true', b'"clean":true,"clean":true'),
            (self.fixture.reconciliation_bytes, b'  "id": "source-reconciliation-v1",',
             b'  "id": "source-reconciliation-v1",\n  "id": "source-reconciliation-v1",'),
            (self.fixture.expectation_bytes, b'  "id": "turnvector-implementation-v2",',
             b'  "id": "turnvector-implementation-v2",\n  "id": "turnvector-implementation-v2",'),
            (self.fixture.ledger_bytes, b'  "id": "fixture-ledger-v1",',
             b'  "id": "fixture-ledger-v1",\n  "id": "fixture-ledger-v1",'),
        ]
        for raw, old, new in replacements:
            with self.subTest(old=old):
                parsed = parse_json_object(raw.replace(old, new, 1), "input")
                self.assertEqual(len(parsed.duplicate_keys), 1)

    def test_float_constant_surrogate_utf8_and_line_endings_are_stage_one_class(self):
        invalid = (b'{"x":1.0}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n',
                   b'{"x":"\\ud800"}\n', b'\xff{}\n', b'{}\r\n', b'{}')
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidCanonicalJson):
                    parse_json_object(raw, "input")

    def test_non_descriptor_type_and_u64_domains_are_not_coerced(self):
        ledger = copy.deepcopy(self.fixture.ledger)
        for value in (True, "12", {}, [], None):
            with self.subTest(value=value):
                candidate = copy.deepcopy(ledger)
                candidate["cardinalities"]["lane_count"] = value
                with self.assertRaises(ContractError):
                    traceability_ledger_value(candidate)
        for value in (-1, 1 << 64):
            candidate = copy.deepcopy(ledger)
            candidate["cardinalities"]["lane_count"] = value
            with self.assertRaises(ContractError):
                traceability_ledger_value(candidate)

    def test_file_type_json_type_totality_for_both_record_kinds(self):
        for path, field in (("source_files", "file_type"),
                            ("historical_objects", "git_object_type")):
            for value in (5, True, None, {}, []):
                with self.subTest(path=path, value=value):
                    snapshot = copy.deepcopy(self.fixture.snapshot)
                    snapshot[path][0][field] = value
                    with self.assertRaises(ContractError):
                        authority_snapshot_value(snapshot)
            for value in ("symlink", "fifo", "directory", "socket", "device", "tree"):
                snapshot = copy.deepcopy(self.fixture.snapshot)
                snapshot[path][0][field] = value
                parsed = authority_snapshot_value(snapshot)
                self.assertEqual(getattr(getattr(parsed, path)[0], field), value,
                                 "semantic file types are deferred to stages 9/10")

    def test_empty_path_and_absolute_path_remain_structurally_distinct(self):
        for value in ("", "/absolute", "../traversal"):
            snapshot = copy.deepcopy(self.fixture.snapshot)
            snapshot["source_files"][0]["path"] = value
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    authority_snapshot_value(snapshot)

    def test_f46_gate_identifiers_are_grammar_valid_and_lexically_ordered(self):
        f46 = build_fixture(variant="f46")
        for lane in f46.expectation["lanes"]:
            gates = [gate["id"] for gate in lane["gates"]]
            self.assertEqual(gates, sorted(gates))
            self.assertTrue(all(len(gate) == 1024 for gate in gates))
        ledger_gates = f46.ledger["entities"]["gates"]
        self.assertEqual(ledger_gates, sorted(ledger_gates,
                                              key=lambda r: (r["lane_id"], r["gate_id"])))


if __name__ == "__main__":
    unittest.main()
