"""Tests for load_source_reconciliation."""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.source_reconciliation import (
    AUTHORITY_FILE_BYTES_MAX,
    SOURCE_RECONCILIATION_V1_SHA256,
    load_source_reconciliation,
)

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "authority" / "source-reconciliation-v1.json"
SCHEMA = ROOT / "schemas" / "source-reconciliation-v1.schema.json"
EXPECTED_ADRS = ["0001", "0018", "0019", "0020", "0029", "0030", "0035"]


def _artifact():
    return copy.deepcopy(json.loads(ARTIFACT.read_text()))


_TOP_LEVEL_ID_LINE = '  "id": "source-reconciliation-v1",'
_OLD_SHA256_LINE = (
    '      "old_sha256": "68f227a43a2367c76e46a088a3a426ed089119454c66c3f24ff9c2321d152512",'
)


def _duplicate_top_level_id_bytes():
    text = ARTIFACT.read_bytes().decode("utf-8")
    return text.replace(
        _TOP_LEVEL_ID_LINE, _TOP_LEVEL_ID_LINE + "\n" + _TOP_LEVEL_ID_LINE
    ).encode("utf-8")


def _duplicate_nested_old_sha256_bytes():
    text = ARTIFACT.read_bytes().decode("utf-8")
    return text.replace(
        _OLD_SHA256_LINE, _OLD_SHA256_LINE + "\n" + _OLD_SHA256_LINE
    ).encode("utf-8")


def _expect_duplicate_key(raw):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reconciliation.json"
        path.write_bytes(raw)
        try:
            load_source_reconciliation(path)
        except ContractError as error:
            if "duplicate" in str(error):
                return
            raise
    raise AssertionError("expected ContractError for duplicate object key")


def _expect_contract_error(mutator):
    payload = _artifact()
    mutator(payload)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reconciliation.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            load_source_reconciliation(path)
        except ContractError:
            return
    raise AssertionError("expected ContractError")


def _swap_mappings(d):
    d["mappings"][0], d["mappings"][1] = d["mappings"][1], d["mappings"][0]


def _upper_revision(d):
    m = d["mappings"][0]
    m["old_revision"] = m["old_revision"].upper()
    m["current_revision"] = m["current_revision"].upper()


def _enum_0030(d):
    m = next(x for x in d["mappings"] if x["adr_number"] == "0030")
    m["classification"] = "material"
    m["disposition"] = "replace_topology_obligations"


def _old_revision_mismatch(d):
    d["mappings"][0]["old_revision"] = "0" * 40


def _current_revision_mismatch(d):
    d["mappings"][0]["current_revision"] = "0" * 40


class LoadSourceReconciliationTests(unittest.TestCase):

    def test_loaded_artifact(self):
        record = load_source_reconciliation(ARTIFACT)
        self.assertEqual(
            record.schema_version, "turnvector.benchmark.source-reconciliation.v1"
        )
        self.assertEqual(record.id, "source-reconciliation-v1")
        self.assertEqual(
            record.predecessor_expectation.id, "turnvector-implementation-v1"
        )
        self.assertEqual(record.target_source.repository, "TurnVector")
        self.assertTrue(record.target_source.clean_required)
        self.assertIsInstance(record.mappings, tuple)
        self.assertEqual([m.adr_number for m in record.mappings], EXPECTED_ADRS)
        self.assertEqual(len(record.mappings), 7)
        adr30 = next(m for m in record.mappings if m.adr_number == "0030")
        self.assertEqual(adr30.classification, "scope_clarification")
        self.assertEqual(adr30.disposition, "retain_with_scope_update")

    def test_canonical_digest(self):
        self.assertEqual(
            hashlib.sha256(ARTIFACT.read_bytes()).hexdigest(),
            SOURCE_RECONCILIATION_V1_SHA256,
        )

    def test_duplicate_top_level_id_key(self):
        _expect_duplicate_key(_duplicate_top_level_id_bytes())

    def test_duplicate_nested_old_sha256_key(self):
        _expect_duplicate_key(_duplicate_nested_old_sha256_bytes())

    def test_path_hash_drift_rejected(self):
        def _mutate(d):
            m0 = d["mappings"][0]
            m1 = d["mappings"][1]
            m0["old_path"] = m1["old_path"]
            m0["old_sha256"] = m1["old_sha256"]

        _expect_contract_error(_mutate)

    def test_summary_drift_rejected(self):
        def _mutate(d):
            d["mappings"][0]["summary"] = (
                "A different but entirely valid nonempty summary."
            )

        _expect_contract_error(_mutate)

    def test_contract_violations(self):
        cases = [
            ("unknown top field", lambda d: d.update({"bogus": 1})),
            ("unknown predecessor field",
             lambda d: d["predecessor_expectation"].update({"bogus": 1})),
            ("missing required field",
             lambda d: d["mappings"][0].pop("adr_number")),
            ("swapped mappings", _swap_mappings),
            ("unknown ADR 9999",
             lambda d: d["mappings"][0].update({"adr_number": "9999"})),
            ("duplicate/wrong lanes",
             lambda d: d["mappings"][1].update(
                 {"affected_lane_ids": d["mappings"][0]["affected_lane_ids"]})),
            ("../ path",
             lambda d: d["mappings"][0].update({"old_path": "../x"})),
            ("absolute path",
             lambda d: d["mappings"][0].update({"old_path": "/etc/passwd"})),
            ("backslash path",
             lambda d: d["mappings"][0].update({"old_path": "docs\\adr\\x.md"})),
            ("double slash path",
             lambda d: d["mappings"][0].update({"old_path": "docs//adr/x.md"})),
            ("trailing slash path",
             lambda d: d["mappings"][0].update({"old_path": "docs/adr/x.md/"})),
            ("dot component path",
             lambda d: d["mappings"][0].update({"old_path": "docs/./x.md"})),
            ("space component path",
             lambda d: d["mappings"][0].update({"old_path": "docs/adr x/x.md"})),
            ("uppercase digest",
             lambda d: d["mappings"][0].update(
                 {"old_sha256": d["mappings"][0]["old_sha256"].upper()})),
            ("uppercase revision", _upper_revision),
            ("wrong enum pair for 0001",
             lambda d: d["mappings"][0].update(
                 {
                     "classification": "scope_clarification",
                     "disposition": "retain_with_scope_update",
                 })),
            ("wrong enum pair for 0030", _enum_0030),
            ("empty summary",
             lambda d: d["mappings"][0].update({"summary": ""})),
            ("1025-char summary",
             lambda d: d["mappings"][0].update({"summary": "a" * 1025})),
            ("false clean_required",
             lambda d: d["target_source"].update({"clean_required": False})),
            ("old_revision mismatch", _old_revision_mismatch),
            ("current_revision mismatch", _current_revision_mismatch),
        ]
        for label, mutator in cases:
            with self.subTest(label):
                _expect_contract_error(mutator)

    def test_schema_smoke(self):
        schema = json.loads(SCHEMA.read_text())
        self.assertFalse(schema["additionalProperties"])
        mappings = schema["properties"]["mappings"]
        self.assertEqual(mappings["minItems"], 7)
        self.assertEqual(mappings["maxItems"], 7)
        self.assertEqual(mappings["minItems"], mappings["maxItems"])
        self.assertIn("$ref", mappings["items"])
        self.assertEqual(mappings["items"]["$ref"], "#/$defs/mapping")
        mapping = schema["$defs"]["mapping"]
        self.assertFalse(mapping["additionalProperties"])
        self.assertEqual(
            mapping["properties"]["adr_number"]["pattern"], r"^[0-9]{4}$"
        )
        self.assertIn("affected_lane_ids", mapping["properties"])
        self.assertIn("summary", mapping["properties"])


class LoadNoFollowTests(unittest.TestCase):
    """The loader must reject symlinks and non-regular inputs no-follow."""

    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target.json"
            target.write_bytes(ARTIFACT.read_bytes())
            link = base / "reconciliation.json"
            os.symlink(target, link)
            with self.assertRaisesRegex(ContractError, "symlink"):
                load_source_reconciliation(link)

    def test_dangling_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "reconciliation.json"
            os.symlink(Path(directory) / "missing.json", link)
            with self.assertRaisesRegex(ContractError, "symlink"):
                load_source_reconciliation(link)

    def test_directory_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.mkdir()
            with self.assertRaisesRegex(ContractError, "regular file"):
                load_source_reconciliation(path)

    def test_fifo_input_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            os.mkfifo(path)
            with self.assertRaisesRegex(ContractError, "regular file"):
                load_source_reconciliation(path)

    def test_oversize_input_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(b"x" * (AUTHORITY_FILE_BYTES_MAX + 1))
            with self.assertRaisesRegex(ContractError, "exceeds"):
                load_source_reconciliation(path)


class LoadRaceIdentityTests(unittest.TestCase):
    """The loader's fstat identity checks detect in-place swap/truncation."""

    def _changed_stat(self, real, size=None, ino=None):
        return os.stat_result(
            (
                real.st_mode,
                real.st_ino if ino is None else ino,
                real.st_dev,
                real.st_nlink,
                real.st_uid,
                real.st_gid,
                real.st_size if size is None else size,
                real.st_atime,
                real.st_mtime,
                real.st_ctime,
            )
        )

    def test_swap_during_open_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(ARTIFACT.read_bytes())
            real = path.lstat()
            with mock.patch(
                "turnvector_benchmark.source_reconciliation.os.fstat",
                return_value=self._changed_stat(real, ino=real.st_ino + 1),
            ):
                with self.assertRaisesRegex(ContractError, "changed while opening"):
                    load_source_reconciliation(path)

    def test_truncation_during_read_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(ARTIFACT.read_bytes())
            real = path.lstat()
            with mock.patch(
                "turnvector_benchmark.source_reconciliation.os.fstat",
                side_effect=[real, self._changed_stat(real, size=real.st_size + 1)],
            ):
                with self.assertRaisesRegex(ContractError, "changed while reading"):
                    load_source_reconciliation(path)


class LoadOSErrorTranslationTests(unittest.TestCase):
    """Raw OSError from fstat/read/close becomes a bounded ContractError."""

    def test_os_error_from_read_is_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(ARTIFACT.read_bytes())
            with mock.patch(
                "turnvector_benchmark.source_reconciliation.os.read",
                side_effect=OSError("boom"),
            ):
                with self.assertRaisesRegex(ContractError, "cannot read"):
                    load_source_reconciliation(path)

    def test_os_error_from_close_is_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(ARTIFACT.read_bytes())
            with mock.patch(
                "turnvector_benchmark.source_reconciliation.os.close",
                side_effect=OSError("boom"),
            ):
                with self.assertRaisesRegex(ContractError, "cannot close"):
                    load_source_reconciliation(path)

    def test_close_error_does_not_mask_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(b"x" * (AUTHORITY_FILE_BYTES_MAX + 1))
            with mock.patch(
                "turnvector_benchmark.source_reconciliation.os.close",
                side_effect=OSError("boom"),
            ):
                with self.assertRaisesRegex(ContractError, "exceeds"):
                    load_source_reconciliation(path)

    def test_final_source_path_resolve_error_is_contract_error(self):
        # The final path.resolve() must translate OSError and RuntimeError
        # into a fixed bounded ContractError rather than leak a raw exception.
        for error in (OSError("boom"), RuntimeError("symlink loop")):
            with self.subTest(error=error):
                with mock.patch.object(
                    Path, "resolve", side_effect=error
                ):
                    with self.assertRaisesRegex(
                        ContractError, "cannot resolve source reconciliation"
                    ):
                        load_source_reconciliation(ARTIFACT)


class LoadSourceEncodingTests(unittest.TestCase):
    """Strict canonical-encoding edge cases: UTF-8, BOM, types, floats."""

    def _expect_reject(self, raw_bytes):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reconciliation.json"
            path.write_bytes(raw_bytes)
            with self.assertRaises(ContractError):
                load_source_reconciliation(path)

    def test_invalid_utf8_rejected(self):
        self._expect_reject(b'\xff\xfe\x00{"schema_version": 1}')

    def test_utf8_bom_rejected(self):
        self._expect_reject(b"\xef\xbb\xbf" + ARTIFACT.read_bytes())

    def test_non_object_root_rejected(self):
        self._expect_reject(b'["not", "an", "object"]')

    def test_float_value_rejected(self):
        payload = _artifact()
        payload["target_source"]["clean_required"] = 1.0
        self._expect_reject(json.dumps(payload).encode("utf-8"))


class SourceReconciliationSchemaCanonicalEncodingTests(unittest.TestCase):
    """The frozen contract's canonical JSON encoding cannot regress."""

    @staticmethod
    def _canonical_bytes(value):
        return json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    def test_schema_file_is_canonical_bytes(self):
        raw = SCHEMA.read_bytes()
        self.assertNotIn(b"\xef\xbb\xbf", raw[:3], "schema must not carry a UTF-8 BOM")
        self.assertNotIn(b"\r", raw, "schema must use LF line endings")
        self.assertTrue(raw.endswith(b"\n"), "schema must end with one final LF")
        self.assertFalse(raw.endswith(b"\n\n"), "schema must end with exactly one final LF")
        obj = json.loads(raw.decode("utf-8"))
        self.assertEqual(raw, self._canonical_bytes(obj))

    def test_schema_object_keys_are_recursively_lexical(self):
        obj = json.loads(SCHEMA.read_text(encoding="utf-8"))
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

    def test_schema_array_order_is_preserved(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["required"],
            [
                "schema_version",
                "id",
                "predecessor_expectation",
                "successor_expectation",
                "target_source",
                "mappings",
                "design_gate_revision",
            ],
        )
        self.assertEqual(
            schema["$defs"]["mapping"]["required"],
            [
                "adr_number",
                "old_revision",
                "old_path",
                "old_sha256",
                "current_revision",
                "current_path",
                "current_sha256",
                "classification",
                "affected_lane_ids",
                "disposition",
                "summary",
            ],
        )


if __name__ == "__main__":
    unittest.main()
