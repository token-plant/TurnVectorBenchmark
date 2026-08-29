"""AuthoritySnapshot and repository-control value-contract tests."""

import copy
import hashlib
import json
import unittest
from pathlib import Path

from turnvector_benchmark.authority.authority_snapshot import (
    EMPTY_SHA256,
    NORMALIZED_FALSE_SHA256,
    NORMALIZED_TRUE_SHA256,
    NORMALIZED_ZERO_SHA256,
    AuthoritySnapshot,
    authority_snapshot_value,
    normalized_value_bytes,
    normalized_value_sha256,
    parse_authority_snapshot,
    repository_control_descriptor,
)
from turnvector_benchmark.authority.bound_bytes import BoundBytesRef
from turnvector_benchmark.authority.contract_json import InvalidCanonicalJson
from turnvector_benchmark.core import ContractError

from tests.fixtures.compiler.fixture_utils import build_fixture, compact

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schemas" / "authority-snapshot-v1.schema.json"


class AuthoritySnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def value(self, snapshot=None):
        raw = compact(snapshot or self.fixture.snapshot)
        parsed = parse_authority_snapshot(AuthoritySnapshot(
            BoundBytesRef(raw), BoundBytesRef(self.fixture.reconciliation_bytes)
        ))
        return authority_snapshot_value(parsed)

    def test_schema_and_descriptor_closed_shapes(self):
        schema = json.loads(SCHEMA.read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(len(schema["required"]), 12)
        descriptor = schema["$defs"]["repository_control_descriptor"]
        self.assertFalse(descriptor["additionalProperties"])
        self.assertEqual(len(descriptor["required"]), 22)
        self.assertEqual(descriptor["properties"]["schema_version"]["const"],
                         "turnvector.benchmark.repository-control.v1")
        self.assertEqual(descriptor["properties"]["strict_parser_version"]["const"],
                         "canonical-strict-parser-v1")

    def test_snapshot_typed_record_shapes_and_range_identity(self):
        value = self.value()
        self.assertEqual(len(value.source_files), 12)
        self.assertEqual(len(value.section_records), 46)
        self.assertEqual(len(value.historical_objects), 14)
        self.assertEqual(len(value.source_files[0].__dataclass_fields__), 5)
        self.assertEqual(len(value.source_files[0].no_follow_identity.__dataclass_fields__), 8)
        self.assertEqual(value.source_files[0].no_follow_identity.size,
                         value.source_files[0].byte_count)
        ranges = [(r.path, r.start, r.end) for r in value.section_records]
        self.assertEqual(len(ranges), len(set(ranges)))
        self.assertTrue(all(start < end for _, start, end in ranges))

    def test_repository_control_root_and_identity_shapes(self):
        descriptor = self.value().repository_control
        self.assertEqual(len(descriptor.__dataclass_fields__), 22)
        for identity in (descriptor.worktree_identity, descriptor.git_dir_identity,
                         descriptor.common_dir_identity):
            self.assertEqual(len(identity.__dataclass_fields__), 9)
            self.assertTrue(identity.absolute_path.startswith("/"))
        roots = descriptor.stability_entries[:3]
        self.assertEqual([r["namespace"] for r in roots], ["worktree", "git_dir", "common_dir"])
        self.assertTrue(all(r["kind"] == "root" and "path" not in r for r in roots))
        self.assertFalse(any(r.get("path", "").startswith("worktrees/")
                             for r in descriptor.stability_entries))

    def test_config_semantic_preimages_and_constants(self):
        cases = [(0, b"0\n", NORMALIZED_ZERO_SHA256),
                 (False, b"false\n", NORMALIZED_FALSE_SHA256),
                 (True, b"true\n", NORMALIZED_TRUE_SHA256)]
        for value, raw, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(normalized_value_bytes(value), raw)
                self.assertEqual(normalized_value_sha256(value), expected)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        with self.assertRaises(ContractError):
            normalized_value_bytes(-1)

    def test_absolute_path_grammar_matrix(self):
        descriptor = copy.deepcopy(self.fixture.snapshot["repository_control"])
        for invalid in ("relative", "/trailing/", "/a//b", "/a/./b", "/a/../b", "/a\x00b"):
            with self.subTest(path=invalid):
                candidate = copy.deepcopy(descriptor)
                candidate["worktree_identity"]["absolute_path"] = invalid
                with self.assertRaises(ContractError):
                    repository_control_descriptor(candidate)
        candidate = copy.deepcopy(descriptor)
        candidate["worktree_identity"]["absolute_path"] = "/"
        self.assertEqual(repository_control_descriptor(candidate).worktree_identity.absolute_path, "/")

    def test_descriptor_wrong_container_scalar_and_identity_types_rejected(self):
        cases = [
            ("control_files", {}), ("config_entries", None), ("clean", 1),
            ("qualified_preflight", "true"), ("stability_sha256", EMPTY_SHA256.upper()),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                descriptor = copy.deepcopy(self.fixture.snapshot["repository_control"])
                descriptor[field] = value
                with self.assertRaises(ContractError):
                    repository_control_descriptor(descriptor)
        descriptor = copy.deepcopy(self.fixture.snapshot["repository_control"])
        descriptor["worktree_identity"]["size"] = True
        with self.assertRaises(ContractError):
            repository_control_descriptor(descriptor)

    def test_both_declared_worktree_kinds_are_structurally_representable(self):
        linked = repository_control_descriptor(self.fixture.snapshot["repository_control"])
        self.assertEqual(linked.worktree_kind, "linked")
        main = copy.deepcopy(self.fixture.snapshot["repository_control"])
        main["worktree_kind"] = "main"
        main["worktree_git_entry"] = {"kind": "directory", "byte_count": 0,
                                      "sha256": EMPTY_SHA256, "no_follow_identity": None}
        parsed = repository_control_descriptor(main)
        self.assertEqual(parsed.worktree_kind, "main")
        self.assertEqual(parsed.worktree_git_entry.kind, "directory")

    def test_scope_and_witness_reference_stores_have_frozen_keys(self):
        descriptor = self.fixture.snapshot["repository_control"]
        self.assertEqual(set(descriptor["recursive_scope_proofs"]), {
            "git-dir-control", "common-dir-control", "common-refs",
            "common-replace-refs", "worktree-control",
        })
        replace = descriptor["recursive_scope_proofs"]["common-replace-refs"]
        self.assertEqual((replace["scope_state"], replace["entry_count"],
                          replace["scope_stability_sha256"]), ("absent", 0, EMPTY_SHA256))
        key = replace["witness_ref"]
        witness = descriptor["component_probe_witnesses"][key]
        locator = {"namespace": witness["namespace"], "requested_path": witness["requested_path"]}
        self.assertEqual(key, hashlib.sha256(compact(locator)).hexdigest())

    def test_invalid_utf8_duplicate_and_noncanonical_bytes_remain_parse_facts(self):
        with self.assertRaises(InvalidCanonicalJson):
            parse_authority_snapshot(AuthoritySnapshot(
                BoundBytesRef(b"\xff" + self.fixture.snapshot_bytes[1:]),
                BoundBytesRef(self.fixture.reconciliation_bytes),
            ))
        raw = self.fixture.snapshot_bytes.replace(b'"clean":true', b'"clean":true,"clean":true', 1)
        parsed = parse_authority_snapshot(AuthoritySnapshot(
            BoundBytesRef(raw), BoundBytesRef(self.fixture.reconciliation_bytes)
        ))
        self.assertEqual(parsed.duplicate_keys[0].key, "clean")


if __name__ == "__main__":
    unittest.main()
