"""Artifact partition and causal key-family tests."""

import unittest
from collections import Counter

from turnvector_benchmark.authority.compiler_relations import compile_relations

from tests.fixtures.compiler.fixture_utils import (
    LANE_CONTEXT_COUNTS,
    LANE_OUTPUT_COUNTS,
    LANE_RAW_COUNTS,
    build_fixture,
)


class CompilerCausalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()
        cls.result = compile_relations(
            {"records": cls.fixture.catalog[1:]}, cls.fixture.expectation,
            cls.fixture.ledger, 8,
        )

    def test_exact_artifact_partition_is_52_plus_21_plus_22(self):
        entities = self.fixture.ledger["entities"]
        counts = (len(entities["evidence_bundle_memberships"]),
                  len(entities["lane_contexts"]),
                  len(entities["post_gate_outputs"]))
        self.assertEqual(counts, (52, 21, 22))
        self.assertEqual(sum(counts), 95)
        self.assertEqual(sum(LANE_RAW_COUNTS), 52)
        self.assertEqual(sum(LANE_CONTEXT_COUNTS), 21)
        self.assertEqual(sum(LANE_OUTPUT_COUNTS), 22)

    def test_context_and_output_roles_are_disjoint(self):
        entities = self.fixture.ledger["entities"]
        context = {r["artifact_type"] for r in entities["lane_contexts"]}
        output = {r["artifact_type"] for r in entities["post_gate_outputs"]}
        self.assertEqual(context, {"manifest", "environment"})
        self.assertEqual(output, {"report", "checksums"})
        self.assertFalse(context.intersection(output))

    def test_every_lane_has_manifest_and_report(self):
        contexts = Counter(r["lane_id"] for r in self.fixture.ledger["entities"]["lane_contexts"])
        outputs = Counter(r["lane_id"] for r in self.fixture.ledger["entities"]["post_gate_outputs"])
        lane_ids = [lane["id"] for lane in self.fixture.expectation["lanes"]]
        self.assertEqual([contexts[lane_id] for lane_id in lane_ids], LANE_CONTEXT_COUNTS)
        self.assertEqual([outputs[lane_id] for lane_id in lane_ids], LANE_OUTPUT_COUNTS)
        lanes = {lane["id"] for lane in self.fixture.expectation["lanes"]}
        self.assertEqual({r["lane_id"] for r in self.fixture.ledger["entities"]["lane_contexts"]
                          if r["artifact_type"] == "manifest"}, lanes)
        self.assertEqual({r["lane_id"] for r in self.fixture.ledger["entities"]["post_gate_outputs"]
                          if r["artifact_type"] == "report"}, lanes)

    def test_all_judge_inputs_are_raw_evidence_sources(self):
        source_ids = set(self.fixture.ledger["entities"]["evidence_source_ids"])
        memberships = self.fixture.ledger["entities"]["evidence_bundle_memberships"]
        self.assertTrue(all(r["evidence_source_id"] in source_ids for r in memberships))
        self.assertEqual(len(source_ids), 41)
        self.assertFalse(source_ids.intersection({"manifest", "environment", "report", "checksums"}))

    def test_43_context_output_keys_reconcile_parent_formula(self):
        keys = self.result.expected_keys.names
        contexts = [key for key in keys if key.startswith("lane-context/")]
        outputs = [key for key in keys if key.startswith("lane-output/")]
        self.assertEqual((len(contexts), len(outputs)), (21, 22))
        validated = self.result.validated
        parent_v7 = 425 + validated.r_he + 3 * validated.h + 4 * 58
        self.assertEqual(validated.k_exec, parent_v7 + 43)

    def test_plan_prefix_key_set_excludes_interruption_and_final_history_fields(self):
        names = self.result.expected_keys.names
        self.assertIn("compile-history", names)
        self.assertIn("run-environment", names)
        self.assertFalse(any("compile-interruption" in name for name in names))
        forbidden = ("final-history-digest", "final_history_sha256", "terminal-history")
        self.assertFalse(any(any(token in name for token in forbidden) for name in names))


if __name__ == "__main__":
    unittest.main()
