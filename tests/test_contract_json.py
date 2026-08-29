"""Focused tests for the private CoverageCompiler JSON boundary."""

import unittest

from turnvector_benchmark.authority.contract_json import (
    InvalidCanonicalJson,
    JsonObject,
    canonical_json_bytes,
    is_canonical_json,
    is_canonical_jsonl,
    parse_json_object,
    parse_jsonl_records,
)


class ContractJsonTests(unittest.TestCase):

    def test_pairs_duplicates_and_array_order_are_retained(self):
        parsed = parse_json_object(
            b'{"z":0,"a":{"k":1,"k":2},"items":[3,1,2]}\n', "ledger"
        )
        self.assertEqual([key for key, _ in parsed.pairs], ["z", "a", "items"])
        nested = parsed.pairs[1][1]
        self.assertIsInstance(nested, JsonObject)
        self.assertEqual(nested.pairs, (("k", 1), ("k", 2)))
        self.assertEqual(parsed.pairs[2][1], (3, 1, 2))
        self.assertEqual(len(parsed.duplicate_keys), 1)
        duplicate = parsed.duplicate_keys[0]
        self.assertEqual(duplicate.path, ("a",))
        self.assertEqual((duplicate.key, duplicate.pair_index, duplicate.occurrence), ("k", 1, 2))

    def test_layout_drift_is_deferred_to_revalidation(self):
        raw = b'{ "z": 1, "a": {"z": 2, "a": 3} }\n'
        parsed = parse_json_object(raw, "snapshot")
        self.assertFalse(is_canonical_json(raw, parsed))
        self.assertEqual(
            canonical_json_bytes(parsed), b'{"a":{"a":3,"z":2},"z":1}\n'
        )

    def test_pretty_serializer_is_recursively_lexical(self):
        value = {"z": 1, "a": {"z": 2, "a": 3}, "items": [{"z": 4, "a": 5}]}
        self.assertEqual(
            canonical_json_bytes(value, indent=2),
            b'{\n  "a": {\n    "a": 3,\n    "z": 2\n  },\n  "items": [\n    {\n      "a": 5,\n      "z": 4\n    }\n  ],\n  "z": 1\n}\n',
        )

    def test_stage_one_lexical_faults_are_typed(self):
        invalid = (
            b'\xef\xbb\xbf{"a":1}\n',
            b'{"a":1}\r\n',
            b'{"a":1}',
            b'{"a":1.0}\n',
            b'{"a":NaN}\n',
            b'{"a":"\\u0061"}\n',
            b'{"a":"\\ud800"}\n',
            b'[1,2]\n',
            b'{"a":1}\n{"b":2}\n',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidCanonicalJson):
                    parse_json_object(raw, "input")

    def test_jsonl_revalidation_preserves_duplicate_fact(self):
        raw = b'{"a":1}\n{"a":1,"a":2}\n'
        records = parse_jsonl_records(raw, "catalog")
        self.assertEqual(len(records), 2)
        self.assertEqual(len(records[1].duplicate_keys), 1)
        self.assertFalse(is_canonical_jsonl(raw, records))
        canonical = b'{"a":1}\n{"b":2}\n'
        self.assertTrue(is_canonical_jsonl(canonical, parse_jsonl_records(canonical, "catalog")))


if __name__ == "__main__":
    unittest.main()
