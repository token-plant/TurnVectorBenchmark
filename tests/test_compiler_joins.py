"""CoverageCompiler relation/join closure and precedence tests."""

import copy
import unittest

from turnvector_benchmark.authority.compiler_relations import (
    RelationValidationError,
    compile_relations,
    validate_relations,
)

from tests.fixtures.compiler.fixture_utils import build_fixture


class CompilerJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def validate(self, fixture=None):
        f = fixture or self.fixture
        return validate_relations({"records": f.catalog[1:]}, f.expectation, f.ledger)

    def assert_variant(self, expected, mutator):
        f = self.fixture.clone()
        mutator(f)
        with self.assertRaises(RelationValidationError) as raised:
            self.validate(f)
        self.assertEqual(raised.exception.variant, expected)
        return raised.exception

    def test_success_closes_all_frozen_counts_before_derivation(self):
        value = self.validate()
        self.assertEqual((value.m, value.h, value.r_he), (59, 2287, 10693))
        self.assertEqual(value.k_exec, 18254)
        self.assertEqual(len(value.lanes), 12)
        self.assertFalse(hasattr(value, "enforcement_paths"))

    def test_complete_derivation_projects_every_path(self):
        result = compile_relations({"records": self.fixture.catalog[1:]},
                                   self.fixture.expectation, self.fixture.ledger, 8)
        d = result.derived
        self.assertEqual(len(d.enforcement_paths), 2287)
        self.assertEqual(len(d.path_case), 2287)
        self.assertEqual(len(d.path_gate), 2287)
        self.assertEqual(len(d.path_judge_negative), 2287)
        self.assertEqual(len(d.path_evidence), 10693)
        self.assertEqual(len(d.gate_aggregate_negative), 58)
        self.assertEqual(len(d.gate_plumbing_negative), 58)

    def test_unknown_reference_precedes_orphan_and_relation_drift(self):
        error = self.assert_variant(
            "traceability_unknown_entity",
            lambda f: f.ledger["entities"]["obligation_to_judge"][0].update({"judge_id": "fixture-unknown-0001"}),
        )
        self.assertIn("judge_id", error.pointers[0])

    def test_orphan_obligation_judge_bundle_gate_and_evidence_variants(self):
        cases = [
            ("traceability_orphan_obligation", lambda f: f.ledger["entities"]["obligation_to_judge"].pop(0)),
            ("traceability_orphan_judge", lambda f: f.ledger["entities"]["judge_ids"].append("fixture-judge-extra-0001")),
            ("traceability_orphan_bundle", lambda f: f.ledger["entities"]["evidence_bundle_memberships"].__setitem__(slice(0, 3), [])),
            ("traceability_orphan_gate", lambda f: f.ledger["entities"]["gates"].append({"lane_id": f.expectation["lanes"][0]["id"], "gate_id": "fixture-gate-extra-0001"})),
            ("traceability_orphan_evidence", lambda f: f.ledger["entities"]["evidence_source_ids"].append("fixture-source-extra-0001")),
        ]
        for expected, mutator in cases:
            with self.subTest(expected=expected):
                self.assert_variant(expected, mutator)

    def test_behavior_case_missing_and_duplicate_pair_fail_closure(self):
        self.assert_variant(
            "traceability_unknown_entity",
            lambda f: f.catalog.pop(1),
        )
        def duplicate(f):
            f.catalog[2]["behavior_case_id"] = f.catalog[1]["behavior_case_id"]
        self.assert_variant("traceability_orphan_case", duplicate)

    def test_duplicate_complete_gate_record_is_stage_30_owner(self):
        self.assert_variant(
            "traceability_duplicate_entity",
            lambda f: f.ledger["entities"]["gates"].insert(1, copy.deepcopy(f.ledger["entities"]["gates"][0])),
        )

    def test_negative_template_wrong_owner_precedes_mapping_count(self):
        self.assert_variant(
            "traceability_unknown_entity",
            lambda f: f.ledger["entities"]["judge_negative_tests"][0].update({"owner_id": "fixture-unknown-0001"}),
        )

    def test_negative_template_count_drift_and_compensated_mapping(self):
        self.assert_variant(
            "traceability_negative_test_coverage_missing",
            lambda f: f.ledger["entities"]["judge_negative_tests"].pop(),
        )
        def compensated(f):
            records = f.ledger["entities"]["judge_negative_tests"]
            records[1]["owner_id"] = records[0]["owner_id"]
            records.sort(key=lambda r: (r["owner_id"], r["id"]))
        self.assert_variant("traceability_negative_test_coverage_missing", compensated)

    def test_stage_38_degree_precedes_stage_40_mapping_defect(self):
        def mutate(f):
            records = f.ledger["entities"]["judge_negative_tests"]
            records[1]["owner_id"] = records[0]["owner_id"]
            records.sort(key=lambda r: (r["owner_id"], r["id"]))
            first_bundle = f.ledger["entities"]["evidence_bundles"][0]["id"]
            f.ledger["entities"]["evidence_bundle_memberships"].extend(
                {"bundle_id": first_bundle, "evidence_source_id": f"fixture-source-overflow-{i:04d}"}
                for i in range(20)
            )
        # Unknown sources are stage 29, so make them admitted too.
        def admitted_mutate(f):
            mutate(f)
            extras = [f"fixture-source-overflow-{i:04d}" for i in range(20)]
            f.ledger["entities"]["evidence_source_ids"].extend(extras)
        self.assert_variant("traceability_degree_cap_exceeded", admitted_mutate)


if __name__ == "__main__":
    unittest.main()
