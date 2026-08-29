"""TraceabilityLedger schema, strict parsing, and immutable-value tests."""

import copy
import json
import unittest
from pathlib import Path

from turnvector_benchmark.authority.bound_bytes import BoundBytesRef
from turnvector_benchmark.authority.contract_json import InvalidCanonicalJson
from turnvector_benchmark.authority.traceability_ledger import (
    LANE_CASE_COUNTS,
    TraceabilityLedger,
    parse_traceability_ledger,
    traceability_ledger_value,
)
from turnvector_benchmark.core import ContractError

from tests.fixtures.compiler.fixture_utils import build_fixture, pretty

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schemas" / "traceability-v1.schema.json"


class TraceabilityLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def value(self, ledger=None):
        raw = pretty(ledger or self.fixture.ledger)
        parsed = parse_traceability_ledger(TraceabilityLedger(BoundBytesRef(raw)))
        return traceability_ledger_value(parsed)

    def assert_value_error(self, mutator, text=None):
        ledger = copy.deepcopy(self.fixture.ledger)
        mutator(ledger)
        with self.assertRaises(ContractError) as raised:
            self.value(ledger)
        if text:
            self.assertIn(text, str(raised.exception))

    def test_schema_is_canonical_strict_and_complete(self):
        raw = SCHEMA.read_bytes()
        schema = json.loads(raw)
        self.assertEqual(raw, json.dumps(schema, indent=2, sort_keys=True).encode() + b"\n")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            {"schema_version", "id", "profile_id", "design_gate_revision", "predecessor", "cardinalities", "binds", "entities"},
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "turnvector.benchmark.traceability.v1")

    def test_negative_template_schema_is_exactly_two_field(self):
        schema = json.loads(SCHEMA.read_text())
        entities = schema["$defs"]["entities"]["properties"]
        for name in ("judge_negative_tests", "aggregate_negative_tests", "plumbing_negative_tests"):
            item = schema["$defs"][entities[name]["items"]["$ref"].split("/")[-1]]
            self.assertFalse(item["additionalProperties"])
            self.assertEqual(set(item["required"]), {"owner_id", "id"})
            self.assertEqual(set(item["properties"]), {"owner_id", "id"})
            self.assertNotIn("template_id", item["properties"])
            self.assertNotIn("gate_id", item["properties"])

    def test_valid_value_has_all_frozen_cardinalities_and_bind_maps(self):
        value = self.value()
        self.assertEqual(value.cardinalities.lane_case_counts, LANE_CASE_COUNTS)
        self.assertEqual(len(value.cardinalities.__dataclass_fields__), 27)
        self.assertEqual(len(value.binds.__dataclass_fields__), 16)
        self.assertEqual(len(value.entities.__dataclass_fields__), 12)
        self.assertEqual(len(value.binds.lane_suite_sha256), 12)
        self.assertEqual(len(value.binds.judge_contract_sha256), 46)
        self.assertEqual(len(value.binds.gate_sha256), 58)

    def test_stage_one_lexical_classes_rejected_before_value_build(self):
        valid = self.fixture.ledger_bytes
        invalid = [
            b"\xff" + valid[1:], valid[:-1], valid.replace(b"\n", b"\r\n", 1),
            valid.replace(b'"lane_count": 12', b'"lane_count": 12.0', 1),
            valid.replace(b'"lane_count": 12', b'"lane_count": NaN', 1),
            valid.replace(b'"fixture-ledger-v1"', b'"\\ud800"', 1),
        ]
        for raw in invalid:
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(InvalidCanonicalJson):
                    parse_traceability_ledger(TraceabilityLedger(BoundBytesRef(raw)))

    def test_pair_parser_retains_duplicate_key_for_dispatcher(self):
        raw = self.fixture.ledger_bytes.replace(
            b'  "id": "fixture-ledger-v1",',
            b'  "id": "fixture-ledger-v1",\n  "id": "fixture-ledger-v1",',
            1,
        )
        parsed = parse_traceability_ledger(TraceabilityLedger(BoundBytesRef(raw)))
        self.assertEqual(len(parsed.duplicate_keys), 1)
        self.assertEqual(parsed.duplicate_keys[0].key, "id")

    def test_unknown_missing_and_mistyped_fields_are_not_coerced(self):
        cases = [
            (lambda d: d.update({"unknown": 1}), "unknown fields"),
            (lambda d: d.pop("id"), "missing required fields"),
            (lambda d: d["cardinalities"].update({"lane_count": "12"}), "integer"),
            (lambda d: d["cardinalities"].update({"lane_count": True}), "integer"),
            (lambda d: d["entities"].update({"judge_ids": {}}), "array"),
        ]
        for mutator, text in cases:
            with self.subTest(text=text):
                self.assert_value_error(mutator, text)

    def test_u64_and_identifier_domains_are_exact(self):
        for value in (-1, 1 << 64, True, "12"):
            with self.subTest(value=value):
                self.assert_value_error(
                    lambda d, value=value: d["cardinalities"].update({"lane_count": value})
                )
        for value in ("Uppercase", "", "bad/id", "bad~id"):
            with self.subTest(value=value):
                self.assert_value_error(lambda d, value=value: d.update({"id": value}))

    def test_predecessor_shape_and_current_lineage_counterexample_are_representable(self):
        predecessor = {"compile_custody_lineage_id": "fixture-prior-lineage-v1", "chronology_sha256": "5" * 64}
        value = self.value({**copy.deepcopy(self.fixture.ledger), "predecessor": predecessor})
        self.assertEqual(value.predecessor.compile_custody_lineage_id, "fixture-prior-lineage-v1")
        self.assert_value_error(
            lambda d: d.update({"predecessor": {"compile_custody_lineage_id": "fixture-prior-lineage-v1"}}),
            "missing required fields",
        )
        # Equality with the current catalog lineage is structurally valid and
        # deliberately deferred to compiler stage 23.
        same = copy.deepcopy(self.fixture.ledger)
        same["predecessor"] = {"compile_custody_lineage_id": "tvb-qualification-d0-catalog-v1", "chronology_sha256": "5" * 64}
        self.assertEqual(self.value(same).predecessor.compile_custody_lineage_id, "tvb-qualification-d0-catalog-v1")

    def test_negative_template_third_field_rejected(self):
        for array_name, third in (("judge_negative_tests", "template_id"),
                                  ("aggregate_negative_tests", "gate_id"),
                                  ("plumbing_negative_tests", "template_id")):
            with self.subTest(array=array_name):
                self.assert_value_error(
                    lambda d, array_name=array_name, third=third:
                    d["entities"][array_name][0].update({third: "fixture-extra-0001"}),
                    "unknown fields",
                )

    def test_order_and_duplicate_facts_survive_structural_value_conversion(self):
        ledger = copy.deepcopy(self.fixture.ledger)
        records = ledger["entities"]["aggregate_negative_tests"]
        records[0], records[1] = records[1], records[0]
        value = self.value(ledger)
        self.assertGreater(value.entities.aggregate_negative_tests[0].owner_id,
                           value.entities.aggregate_negative_tests[1].owner_id)
        ledger = copy.deepcopy(self.fixture.ledger)
        ledger["entities"]["judge_ids"].append(ledger["entities"]["judge_ids"][0])
        value = self.value(ledger)
        self.assertEqual(value.entities.judge_ids.count(value.entities.judge_ids[0]), 2)


if __name__ == "__main__":
    unittest.main()
