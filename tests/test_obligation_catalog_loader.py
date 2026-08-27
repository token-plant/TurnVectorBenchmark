"""Tests for load_obligation_catalog: parsing, binding data, algebra."""

import copy
import hashlib
import tempfile
import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.obligation_catalog import (
    EXACT_LANE_OBLIGATION_COUNTS,
    ADAPTER_BLOCKED_STATUS,
    ENVIRONMENT_BLOCKED_STATUS,
    OUT_OF_SCOPE_STATUS,
    READY_STATUS,
    CatalogPredecessor,
    _check_readiness_algebra,
    load_obligation_catalog,
)
from tests.obligation_catalog_test_utils import (
    CATALOG,
    SYNTHETIC_CATALOG_SHA256,
    expect_contract_error,
    expect_raw_contract_error,
    fixture_objects,
    load_variant,
    raw_variant,
    write_variant,
)


def _swap_first_two(objects):
    objects[1], objects[2] = objects[2], objects[1]


def _raw_variant_of(mutator):
    """Build raw invalid JSONL bytes from a mutated fixture, bypassing the
    canonical serializer's input validation."""
    objects = fixture_objects()
    mutator(objects)
    return raw_variant(objects)


class LoadedSyntheticCatalogTests(unittest.TestCase):

    def test_loaded_artifact(self):
        catalog = load_obligation_catalog(CATALOG)
        header = catalog.header
        self.assertEqual(header.kind, "catalog")
        self.assertEqual(header.schema_version, "turnvector.benchmark.obligation-catalog.v1")
        self.assertEqual(header.id, "synthetic-obligation-catalog-v1")
        self.assertEqual(header.profile_id, "turnvector-implementation-v2")
        self.assertEqual(header.compile_custody_lineage_id, "tvb-qualification-d0-catalog-v1")
        self.assertEqual(header.t_max, 8)
        self.assertEqual(header.required_obligation_count, 46)
        self.assertEqual(header.record_count, 48)
        self.assertIsNone(header.predecessor)
        self.assertEqual(len(catalog.obligations), 47)
        self.assertEqual(len(catalog.required_obligations), 46)

    def test_canonical_digest(self):
        self.assertEqual(
            hashlib.sha256(CATALOG.read_bytes()).hexdigest(), SYNTHETIC_CATALOG_SHA256
        )
        catalog = load_obligation_catalog(CATALOG)
        self.assertEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)
        self.assertEqual(catalog.file_size, CATALOG.stat().st_size)
        self.assertTrue(catalog.source_path.is_absolute())

    def test_obligation_ids_sorted_and_unique(self):
        catalog = load_obligation_catalog(CATALOG)
        ids = [record.id for record in catalog.obligations]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_per_lane_required_counts_exact(self):
        catalog = load_obligation_catalog(CATALOG)
        counts = {lane: 0 for lane in EXACT_LANE_OBLIGATION_COUNTS}
        for record in catalog.required_obligations:
            counts[record.lane_id] += 1
        self.assertEqual(counts, dict(EXACT_LANE_OBLIGATION_COUNTS))

    def test_readiness_algebra_in_fixture(self):
        catalog = load_obligation_catalog(CATALOG)
        statuses = {record.readiness_status for record in catalog.required_obligations}
        self.assertTrue(statuses <= {"design_ready", "adapter_blocked", "environment_blocked"})
        for record in catalog.required_obligations:
            if record.readiness_status == "design_ready":
                self.assertEqual(record.blocker_ids, ())
            else:
                self.assertTrue(record.blocker_ids)
        optional = [record for record in catalog.obligations if not record.required]
        self.assertEqual(len(optional), 1)
        self.assertEqual(optional[0].readiness_status, "intentionally_out_of_scope")
        self.assertEqual(optional[0].blocker_ids, ())

    def test_custody_binding_data_preserved(self):
        catalog = load_obligation_catalog(CATALOG)
        header = catalog.header
        self.assertEqual(header.custody_domain_id, "synthetic-custody-domain-v1")
        self.assertEqual(header.custody_domain_sha256, "34" * 32)
        self.assertEqual(header.lineage_id, "synthetic-obligation-catalog-lineage-v1")
        self.assertEqual(header.design_gate_revision, "ab" * 32)
        self.assertEqual(header.source_reconciliation_sha256, "cd" * 32)
        self.assertEqual(header.expectation_sha256, "ef" * 32)
        self.assertEqual(header.compile_custody_policy_sha256, "12" * 32)


class CustodyBindingMutationTests(unittest.TestCase):
    """Binding fields are carried verbatim and mutate the catalog digest."""

    def _mutate_and_reload(self, mutator):
        objects = fixture_objects()
        mutator(objects[0])
        with tempfile.TemporaryDirectory() as directory:
            return load_obligation_catalog(write_variant(objects, directory))

    def test_custody_domain_id_mutation_changes_digest(self):
        catalog = self._mutate_and_reload(
            lambda header: header.update({"custody_domain_id": "other-domain-v1"})
        )
        self.assertEqual(catalog.header.custody_domain_id, "other-domain-v1")
        self.assertNotEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)

    def test_custody_domain_sha256_mutation_changes_digest(self):
        catalog = self._mutate_and_reload(
            lambda header: header.update({"custody_domain_sha256": "99" * 32})
        )
        self.assertEqual(catalog.header.custody_domain_sha256, "99" * 32)
        self.assertNotEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)

    def test_lineage_id_mutation_changes_digest(self):
        catalog = self._mutate_and_reload(
            lambda header: header.update({"lineage_id": "other-lineage-v1"})
        )
        self.assertEqual(catalog.header.lineage_id, "other-lineage-v1")
        self.assertNotEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)

    def test_predecessor_binding_is_carried(self):
        predecessor = {
            "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v0",
            "chronology_sha256": "77" * 32,
        }
        catalog = self._mutate_and_reload(
            lambda header: header.update({"predecessor": predecessor})
        )
        self.assertEqual(
            catalog.header.predecessor,
            CatalogPredecessor(
                compile_custody_lineage_id="tvb-qualification-d0-catalog-v0",
                chronology_sha256="77" * 32,
            ),
        )
        self.assertNotEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)

    def test_expectation_sha256_mutation_changes_digest(self):
        catalog = self._mutate_and_reload(
            lambda header: header.update({"expectation_sha256": "55" * 32})
        )
        self.assertEqual(catalog.header.expectation_sha256, "55" * 32)
        self.assertNotEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)


class HeaderContractViolationTests(unittest.TestCase):

    def test_frozen_constants(self):
        cases = [
            ("schema_version", "turnvector.benchmark.obligation-catalog.v9"),
            ("profile_id", "turnvector-implementation-v1"),
            ("compile_custody_lineage_id", "tvb-qualification-d0-catalog-v2"),
            ("t_max", 7),
            ("t_max", 9),
            ("required_obligation_count", 45),
            ("required_obligation_count", 47),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                expect_contract_error(
                    lambda objects, field=field, value=value: objects[0].update({field: value})
                )

    def test_record_count_mismatch(self):
        expect_contract_error(lambda objects: objects[0].update({"record_count": 47}))
        expect_contract_error(lambda objects: objects[0].update({"record_count": 49}))

    def test_unknown_header_field(self):
        expect_contract_error(lambda objects: objects[0].update({"bogus": 1}))

    def test_missing_header_field(self):
        expect_contract_error(lambda objects: objects[0].pop("record_count"))

    def test_header_id_format(self):
        expect_contract_error(lambda objects: objects[0].update({"id": "No!"}))
        expect_contract_error(lambda objects: objects[0].update({"id": ""}))

    def test_digest_format(self):
        for field in (
            "design_gate_revision",
            "source_reconciliation_sha256",
            "expectation_sha256",
            "compile_custody_policy_sha256",
            "custody_domain_sha256",
        ):
            with self.subTest(field=field):
                expect_contract_error(
                    lambda objects, field=field: objects[0].update({field: "0" * 63})
                )

    def test_custody_domain_id_format(self):
        expect_contract_error(lambda objects: objects[0].update({"custody_domain_id": ""}))

    def test_predecessor_equal_current_q_rejected(self):
        # A predecessor lineage q equal to the current header
        # compile_custody_lineage_id cannot name a preserved predecessor.
        expect_contract_error(
            lambda objects: objects[0].update(
                {
                    "predecessor": {
                        "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v1",
                        "chronology_sha256": "77" * 32,
                    }
                }
            ),
            message="must differ from the current header",
        )

    def test_predecessor_object_missing_field_rejected(self):
        expect_contract_error(
            lambda objects: objects[0].update(
                {
                    "predecessor": {
                        "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v0"
                    }
                }
            ),
            message="missing required fields",
        )

    def test_predecessor_object_unknown_field_rejected(self):
        expect_contract_error(
            lambda objects: objects[0].update(
                {
                    "predecessor": {
                        "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v0",
                        "chronology_sha256": "77" * 32,
                        "extra": 1,
                    }
                }
            ),
            message="unknown fields",
        )

    def test_predecessor_object_bad_identifier_rejected(self):
        for bad in ("../x", "", "Not-An-Id", "has space"):
            with self.subTest(bad=bad):
                expect_contract_error(
                    lambda objects, bad=bad: objects[0].update(
                        {
                            "predecessor": {
                                "compile_custody_lineage_id": bad,
                                "chronology_sha256": "77" * 32,
                            }
                        }
                    ),
                    message="compile_custody_lineage_id",
                )

    def test_predecessor_object_bad_digest_rejected(self):
        for bad in ("0" * 63, "A" * 64, "", "0" * 65):
            with self.subTest(bad=bad):
                expect_contract_error(
                    lambda objects, bad=bad: objects[0].update(
                        {
                            "predecessor": {
                                "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v0",
                                "chronology_sha256": bad,
                            }
                        }
                    ),
                    message="chronology_sha256",
                )

    def test_predecessor_non_object_rejected(self):
        for bad in ("predecessor-catalog-v0", ["x"], 42, True):
            with self.subTest(bad=bad):
                expect_contract_error(
                    lambda objects, bad=bad: objects[0].update({"predecessor": bad}),
                    message="null or an object",
                )

    def test_predecessor_null_round_trip(self):
        catalog = load_obligation_catalog(CATALOG)
        self.assertIsNone(catalog.header.predecessor)
        self.assertEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)

    def test_predecessor_object_round_trip(self):
        predecessor = {
            "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v0",
            "chronology_sha256": "88" * 32,
        }
        catalog = load_variant(
            lambda objects: objects[0].update({"predecessor": predecessor})
        )
        self.assertIsInstance(catalog.header.predecessor, CatalogPredecessor)
        self.assertEqual(
            catalog.header.predecessor.compile_custody_lineage_id,
            "tvb-qualification-d0-catalog-v0",
        )
        self.assertEqual(catalog.header.predecessor.chronology_sha256, "88" * 32)
        self.assertNotEqual(catalog.file_sha256, SYNTHETIC_CATALOG_SHA256)

    def test_header_not_first(self):
        expect_contract_error(_swap_first_two)

    def test_second_header_record_rejected(self):
        expect_contract_error(
            lambda objects: objects.append(copy.deepcopy(objects[0]))
        )

    def test_kind_must_be_catalog(self):
        expect_contract_error(lambda objects: objects[0].update({"kind": "catalogue"}))


class ObligationContractViolationTests(unittest.TestCase):

    def _mutate_obligation(self, mutator):
        expect_contract_error(lambda objects: mutator(objects[1]))

    def test_unknown_field(self):
        self._mutate_obligation(lambda o: o.update({"bogus": 1}))

    def test_missing_field(self):
        self._mutate_obligation(lambda o: o.pop("module_ids"))

    def test_kind_must_be_obligation(self):
        self._mutate_obligation(lambda o: o.update({"kind": "obligations"}))

    def test_required_must_be_boolean(self):
        self._mutate_obligation(lambda o: o.update({"required": 1}))

    def test_required_float_rejected_at_parse(self):
        # The canonical serializer rejects floats up front, so manufacture the
        # raw invalid bytes directly to prove the loader's parser rejects them.
        expect_raw_contract_error(
            _raw_variant_of(lambda objects: objects[1].update({"required": 1.0}))
        )

    def test_section_range_violations(self):
        self._mutate_obligation(lambda o: o.update({"section_start": -1}))
        self._mutate_obligation(lambda o: o.update({"section_start": 1 << 64}))
        # zero-length and reversed ranges
        self._mutate_obligation(
            lambda o: o.update({"section_end": o["section_start"]})
        )
        self._mutate_obligation(
            lambda o: o.update({"section_start": o["section_end"] + 1})
        )

    def test_section_start_float_rejected_at_parse(self):
        # Raw invalid bytes again: a float section offset must be rejected by
        # the loader parser, not by the canonical serializer.
        expect_raw_contract_error(
            _raw_variant_of(lambda objects: objects[1].update({"section_start": 1.5}))
        )

    def test_path_violations(self):
        for bad in (
            "/etc/passwd",
            "../escape.md",
            "a/../b.md",
            "a//b.md",
            "a/./b.md",
            "a/b/",
            "a\\b.md",
            "",
            "docs/adr x/x.md",
        ):
            with self.subTest(bad=bad):
                self._mutate_obligation(lambda o, bad=bad: o.update({"source_path": bad}))

    def test_identifier_fields(self):
        # claim_class is prose, not an identifier; the id/*_id/*_ids element
        # fields keep identifier grammar.
        for field in ("id", "seam_id", "lane_id", "behavior_case_id"):
            with self.subTest(field=field):
                self._mutate_obligation(lambda o, field=field: o.update({field: "Bad!"}))

    def test_lane_id_must_be_known_lane(self):
        self._mutate_obligation(lambda o: o.update({"lane_id": "not-a-lane"}))

    def test_digest_fields(self):
        for field in ("source_file_sha256", "section_sha256", "design_gate_revision"):
            with self.subTest(field=field):
                self._mutate_obligation(
                    lambda o, field=field: o.update({field: "A" * 64})
                )

    def test_prose_fields_empty_or_non_string_rejected(self):
        for field in ("claim_class", "observable_seam", "evidence_grade", "invalidation_rule"):
            with self.subTest(field=field, value=""):
                self._mutate_obligation(lambda o, field=field: o.update({field: ""}))
            with self.subTest(field=field, value=42):
                self._mutate_obligation(lambda o, field=field: o.update({field: 42}))

    def test_claim_class_accepts_non_identifier_prose(self):
        # claim_class is a nonempty Unicode prose string, not an identifier:
        # punctuation, spaces, uppercase, and non-ASCII are all accepted.
        catalog = load_variant(
            lambda objects: objects[1].update(
                {"claim_class": "Claim class prose: evidence from production logs."}
            )
        )
        self.assertEqual(
            catalog.obligations[0].claim_class,
            "Claim class prose: evidence from production logs.",
        )

    def test_prose_fields_accept_long_and_multibyte_values(self):
        # No per-field 4096/1024 byte maximum exists; long and multibyte prose
        # is accepted inside the whole-file canonical cap.
        long_prose = "x" * 5000
        multibyte = "可观测接缝说明: 中文证据等级与失效规则"
        catalog = load_variant(
            lambda objects: objects[1].update(
                {
                    "observable_seam": long_prose,
                    "evidence_grade": multibyte,
                    "invalidation_rule": long_prose + multibyte,
                }
            )
        )
        record = catalog.obligations[0]
        self.assertEqual(record.observable_seam, long_prose)
        self.assertEqual(record.evidence_grade, multibyte)
        self.assertEqual(record.invalidation_rule, long_prose + multibyte)
        self.assertLess(catalog.file_size, 1 << 20)

    def test_module_ids(self):
        self._mutate_obligation(lambda o: o.update({"module_ids": []}))
        self._mutate_obligation(
            lambda o: o.update({"module_ids": ["m", "m"]})
        )
        self._mutate_obligation(lambda o: o.update({"module_ids": "m"}))

    def test_blocker_ids_duplicates(self):
        self._mutate_obligation(
            lambda o: o.update({"blocker_ids": ["b", "b"]})
        )


