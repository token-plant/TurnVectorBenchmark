"""Tests for the obligation-catalog contract schema files."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER_SCHEMA = ROOT / "schemas" / "obligation-catalog-header-v1.schema.json"
RECORD_SCHEMA = ROOT / "schemas" / "obligation-record-v1.schema.json"

HEADER_REQUIRED = [
    "kind",
    "schema_version",
    "id",
    "profile_id",
    "lineage_id",
    "predecessor",
    "design_gate_revision",
    "source_reconciliation_sha256",
    "expectation_sha256",
    "compile_custody_policy_sha256",
    "custody_domain_id",
    "custody_domain_sha256",
    "compile_custody_lineage_id",
    "t_max",
    "required_obligation_count",
    "record_count",
]

RECORD_REQUIRED = [
    "kind",
    "id",
    "required",
    "claim_class",
    "source_path",
    "source_file_sha256",
    "section_start",
    "section_end",
    "section_sha256",
    "module_ids",
    "seam_id",
    "observable_seam",
    "evidence_grade",
    "invalidation_rule",
    "lane_id",
    "behavior_case_id",
    "readiness_status",
    "blocker_ids",
    "design_gate_revision",
]


class SchemaCanonicalEncodingTests(unittest.TestCase):
    """The frozen contract's canonical JSON encoding cannot regress."""

    @staticmethod
    def _canonical_bytes(value):
        return json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    def test_header_schema_is_canonical_bytes(self):
        raw = HEADER_SCHEMA.read_bytes()
        self.assertNotIn(b"\xef\xbb\xbf", raw[:3], "schema must not carry a UTF-8 BOM")
        self.assertNotIn(b"\r", raw, "schema must use LF line endings")
        self.assertTrue(raw.endswith(b"\n"), "schema must end with one final LF")
        self.assertFalse(raw.endswith(b"\n\n"), "schema must end with exactly one final LF")
        obj = json.loads(raw.decode("utf-8"))
        self.assertEqual(raw, self._canonical_bytes(obj))

    def test_record_schema_is_canonical_bytes(self):
        raw = RECORD_SCHEMA.read_bytes()
        self.assertNotIn(b"\xef\xbb\xbf", raw[:3], "schema must not carry a UTF-8 BOM")
        self.assertNotIn(b"\r", raw, "schema must use LF line endings")
        self.assertTrue(raw.endswith(b"\n"), "schema must end with one final LF")
        self.assertFalse(raw.endswith(b"\n\n"), "schema must end with exactly one final LF")
        obj = json.loads(raw.decode("utf-8"))
        self.assertEqual(raw, self._canonical_bytes(obj))

    def test_schema_object_keys_are_recursively_lexical(self):
        for path in (HEADER_SCHEMA, RECORD_SCHEMA):
            with self.subTest(path=path.name):
                obj = json.loads(path.read_text(encoding="utf-8"))
                stack = [("schema", obj)]
                while stack:
                    where, value = stack.pop()
                    if isinstance(value, dict):
                        keys = list(value)
                        self.assertEqual(
                            keys, sorted(keys), f"{where} object keys are not lexically ordered"
                        )
                        for key, child in value.items():
                            stack.append((f"{where}.{key}", child))
                    elif isinstance(value, list):
                        for index, child in enumerate(value):
                            stack.append((f"{where}[{index}]", child))


class HeaderSchemaContractTests(unittest.TestCase):

    def test_frozen_consts(self):
        props = json.loads(HEADER_SCHEMA.read_text(encoding="utf-8"))["properties"]
        self.assertEqual(props["kind"]["const"], "catalog")
        self.assertEqual(
            props["schema_version"]["const"], "turnvector.benchmark.obligation-catalog.v1"
        )
        self.assertEqual(props["profile_id"]["const"], "turnvector-implementation-v2")
        self.assertEqual(
            props["compile_custody_lineage_id"]["const"], "tvb-qualification-d0-catalog-v1"
        )
        self.assertEqual(props["t_max"]["const"], 8)
        self.assertEqual(props["required_obligation_count"]["const"], 46)

    def test_header_strict_shape(self):
        schema = json.loads(HEADER_SCHEMA.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], HEADER_REQUIRED)
        self.assertEqual(
            set(schema["properties"]), set(HEADER_REQUIRED), "every property must be required"
        )
        self.assertEqual(
            schema["$defs"]["predecessor"]["anyOf"],
            [{"type": "null"}, {"$ref": "#/$defs/catalog_predecessor"}],
        )
        predecessor = schema["$defs"]["catalog_predecessor"]
        self.assertFalse(predecessor["additionalProperties"])
        self.assertEqual(
            predecessor["required"], ["chronology_sha256", "compile_custody_lineage_id"]
        )
        self.assertEqual(
            predecessor["properties"],
            {
                "chronology_sha256": {"$ref": "#/$defs/sha256"},
                "compile_custody_lineage_id": {"$ref": "#/$defs/id"},
            },
        )
        self.assertEqual(predecessor["type"], "object")
        self.assertEqual(
            schema["$defs"]["id"]["pattern"], r"^[a-z0-9][a-z0-9._-]*$"
        )
        self.assertEqual(
            schema["$defs"]["sha256"]["pattern"], r"^[0-9a-f]{64}$"
        )

    def test_header_u64_definitions(self):
        schema = json.loads(HEADER_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$defs"]["u64"],
            {"maximum": 18446744073709551615, "minimum": 0, "type": "integer"},
        )
        record_count = schema["properties"]["record_count"]
        self.assertEqual(record_count["$ref"], "#/$defs/u64")
        self.assertEqual(record_count["minimum"], 1)


class RecordSchemaContractTests(unittest.TestCase):

    def test_record_strict_shape(self):
        schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], RECORD_REQUIRED)
        self.assertEqual(
            set(schema["properties"]), set(RECORD_REQUIRED), "every property must be required"
        )
        props = schema["properties"]
        self.assertEqual(props["kind"]["const"], "obligation")
        self.assertEqual(
            schema["$defs"]["readiness_status"]["enum"],
            [
                "adapter_blocked",
                "design_ready",
                "environment_blocked",
                "intentionally_out_of_scope",
            ],
        )
        self.assertEqual(props["readiness_status"], {"$ref": "#/$defs/readiness_status"})
        self.assertEqual(props["source_path"], {"$ref": "#/$defs/posix_path"})
        self.assertEqual(
            schema["$defs"]["posix_path"]["pattern"],
            r"^(?!\.(?:/|$))(?!\.\.(?:/|$))(?!.*//)(?!.*/\.(?!\.)(?:/|$))(?!.*/\.\.(?:/|$))"
            r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$",
        )
        self.assertEqual(props["required"]["type"], "boolean")
        self.assertEqual(props["module_ids"], {"$ref": "#/$defs/identifier_array"})
        self.assertEqual(schema["$defs"]["identifier_array"]["minItems"], 1)
        self.assertTrue(schema["$defs"]["identifier_array"]["uniqueItems"])
        self.assertEqual(props["blocker_ids"], {"$ref": "#/$defs/blocker_ids"})
        self.assertTrue(schema["$defs"]["blocker_ids"]["uniqueItems"])
        self.assertNotIn(
            "minItems", schema["$defs"]["blocker_ids"], "blocker_ids may be empty"
        )

    def test_lane_enum_is_exact_successor_lanes(self):
        schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["properties"]["lane_id"]["enum"],
            [
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
            ],
        )

    def test_readiness_truth_table_if_then(self):
        """The schema encodes the complete four-state readiness/blocker table."""
        schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["if"]["properties"]["required"], {"const": True})
        self.assertEqual(
            schema["if"]["properties"]["readiness_status"], {"const": "design_ready"}
        )
        self.assertEqual(
            schema["then"]["properties"]["blocker_ids"], {"maxItems": 0}
        )
        branch = schema["else"]
        self.assertEqual(
            branch["if"]["properties"]["readiness_status"], {"const": "adapter_blocked"}
        )
        self.assertEqual(
            branch["then"]["properties"]["blocker_ids"], {"minItems": 1}
        )
        branch = branch["else"]
        self.assertEqual(
            branch["if"]["properties"]["readiness_status"], {"const": "environment_blocked"}
        )
        self.assertEqual(
            branch["then"]["properties"]["blocker_ids"], {"minItems": 1}
        )
        branch = branch["else"]
        self.assertEqual(
            branch["if"]["properties"]["readiness_status"],
            {"const": "intentionally_out_of_scope"},
        )
        self.assertEqual(
            branch["then"]["properties"]["blocker_ids"], {"maxItems": 0}
        )
        self.assertIs(branch["else"], False)

    def test_field_types(self):
        schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        props = schema["properties"]
        for name in ("section_start", "section_end"):
            self.assertEqual(props[name], {"$ref": "#/$defs/u64"})
        self.assertEqual(
            schema["$defs"]["u64"],
            {"maximum": 18446744073709551615, "minimum": 0, "type": "integer"},
        )
        for name in ("id", "seam_id", "behavior_case_id"):
            self.assertEqual(props[name], {"$ref": "#/$defs/id"})
        for name in (
            "claim_class",
            "observable_seam",
            "evidence_grade",
            "invalidation_rule",
        ):
            self.assertEqual(props[name], {"$ref": "#/$defs/nonempty_string"})
        self.assertEqual(
            schema["$defs"]["nonempty_string"],
            {"minLength": 1, "type": "string"},
        )
        for name in (
            "source_file_sha256",
            "section_sha256",
            "design_gate_revision",
        ):
            self.assertEqual(props[name], {"$ref": "#/$defs/sha256"})

    def test_no_per_field_max_length_on_prose_fields(self):
        # The removed 4096/1024 character maxima must not reappear: prose
        # fields are bounded only by the whole-file byte cap, and JSON Schema
        # maxLength would misstate UTF-8 bytes as characters.
        schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        for name in ("claim_class", "observable_seam", "evidence_grade", "invalidation_rule"):
            with self.subTest(name=name):
                self.assertNotIn("maxLength", schema["$defs"]["nonempty_string"])
                self.assertNotIn("maxLength", schema["properties"][name])


class SchemaRuntimeResponsibilitySplitTests(unittest.TestCase):
    """The schemas validate one record in isolation; the runtime is the
    authority for every cross-record comparison JSON Schema cannot express.

    A file passing one record schema alone is not an accepted catalog: total
    record_count, per-lane counts, sorted/unique ids, (lane_id,
    behavior_case_id) pair uniqueness, design_gate_revision equality, and
    current-vs-predecessor q inequality are enforced by the loader tests.
    """

    def test_record_schema_has_no_cross_record_rules(self):
        schema = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
        props = schema["properties"]
        for name in (
            "record_count",
            "required_obligation_count",
            "compile_custody_lineage_id",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, props)
        # No per-lane count, sortedness, or pair-uniqueness machinery exists.
        self.assertNotIn("allOf", schema)
        self.assertNotIn("minItems", schema["properties"]["lane_id"])
        self.assertNotIn("patternProperties", schema)

    def test_header_schema_has_no_per_lane_or_pair_rules(self):
        schema = json.loads(HEADER_SCHEMA.read_text(encoding="utf-8"))
        props = schema["properties"]
        for name in (
            "lane_id",
            "behavior_case_id",
            "module_ids",
            "blocker_ids",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, props)
        # record_count is a single u64 >= 1; the schema cannot express the
        # total-record equality, which the loader enforces.
        self.assertEqual(props["record_count"], {"$ref": "#/$defs/u64", "minimum": 1})
        self.assertNotIn("allOf", schema)

    def test_runtime_tests_cover_the_cross_record_rules(self):
        # The loader/schema tests below must exist and enforce the split.
        import tests.test_obligation_catalog_counts as counts
        import tests.test_obligation_catalog_loader as loader

        self.assertTrue(
            hasattr(counts.CountAndOrderViolationTests, "test_duplicate_lane_behavior_pair_rejected")
        )
        self.assertTrue(
            hasattr(counts.CountAndOrderViolationTests, "test_per_lane_count_mismatch_rejected")
        )
        self.assertTrue(
            hasattr(loader.DesignGateRevisionTests, "test_mixed_obligation_revision_rejected")
        )
        self.assertTrue(
            hasattr(loader.OptionalPairUniquenessTests, "test_required_optional_duplicate_pair_rejected")
        )
        self.assertTrue(
            hasattr(loader.HeaderContractViolationTests, "test_predecessor_equal_current_q_rejected")
        )


if __name__ == "__main__":
    unittest.main()
