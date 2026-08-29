"""Canonical compiler/run key grammar and accounting tests."""

import hashlib
import unittest

from turnvector_benchmark.authority.compiler_relations import (
    compile_relations,
    decode_enforcement_path,
    encode_enforcement_path,
    pct_decode,
    pct_encode,
    validate_key,
)

from tests.fixtures.compiler.fixture_utils import build_fixture


class CompilerKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()
        cls.result = compile_relations({"records": cls.fixture.catalog[1:]},
                                       cls.fixture.expectation, cls.fixture.ledger, 8)

    def test_percent_encoding_is_minimal_uppercase_and_round_trips(self):
        self.assertEqual(pct_encode("abc-._09"), "abc-._09")
        self.assertEqual(pct_encode("a/b%~"), "a%2Fb%25%7E")
        self.assertEqual(pct_decode("a%2Fb%25%7E"), "a/b%~")
        for invalid in ("%", "%2f", "%GG", "%41", "%FF"):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    pct_decode(invalid)

    def test_five_field_enforcement_path_is_injective(self):
        fields = ("fixture-obligation-0001", "lane.matrix.0001", "fixture-judge-0001",
                  "fixture-bundle-0001", "fixture-gate-0001")
        encoded = encode_enforcement_path(*fields)
        self.assertEqual(encoded.count("~"), 4)
        self.assertEqual(decode_enforcement_path(encoded), fields)
        with self.assertRaises(ValueError):
            decode_enforcement_path(encoded + "~extra")

    def test_complete_name_array_is_sorted_unique_and_digest_bound(self):
        expected = self.result.expected_keys
        self.assertEqual(expected.names, tuple(sorted(expected.names, key=lambda x: x.encode("ascii"))))
        self.assertEqual(len(expected.names), len(set(expected.names)))
        preimage = b"".join(name.encode("ascii") + b"\n" for name in expected.names)
        self.assertEqual(hashlib.sha256(preimage).hexdigest(), expected.sha256)
        self.assertEqual(len(preimage), expected.preimage_byte_count)
        self.assertEqual(expected.names_array_byte_count,
                         expected.preimage_byte_count + 2 * expected.count + 1)

    def test_exact_family_counts_and_k_run_formula(self):
        expected = self.result.expected_keys
        self.assertEqual(expected.compile_key_count, 17)
        self.assertEqual(expected.execution_key_count, 18_254)
        self.assertEqual(expected.run_global_key_count, 5)
        self.assertEqual(expected.count, expected.execution_key_count + 2 * 8 + 6)
        self.assertEqual(expected.count, 18_276)

    def test_all_keys_are_nonempty_and_validate(self):
        for key in self.result.expected_keys.names:
            family, components = validate_key(key)
            self.assertTrue(family)
            self.assertGreaterEqual(len(key.encode("ascii")) + 1, 2)
        self.assertLessEqual(self.result.expected_keys.count,
                             self.result.expected_keys.preimage_byte_count)

    def test_wrong_arity_noncanonical_attempt_and_unknown_family_rejected(self):
        for key in ("case", "case/a/b", "compile-attempt/01", "compile-output/0",
                    "unknown/value", "lane-context/lane"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_key(key)

    def test_template_record_id_and_owner_id_drive_negative_keys(self):
        entities = self.fixture.ledger["entities"]
        judge = entities["judge_negative_tests"][0]
        aggregate = entities["aggregate_negative_tests"][0]
        names = self.result.expected_keys.names
        self.assertTrue(any(key.startswith("judge-negative/") and key.endswith("/" + judge["id"])
                            for key in names))
        self.assertIn(f"gate-negative/{aggregate['owner_id']}/{aggregate['id']}", names)
        self.assertNotIn("template_id", aggregate)
        self.assertNotIn("gate_id", aggregate)


if __name__ == "__main__":
    unittest.main()
