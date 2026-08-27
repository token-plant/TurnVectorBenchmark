"""Tests for strict canonical contract encoding and no-follow bounded IO."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turnvector_benchmark.canonical import (
    canonical_jsonl_line,
    parse_canonical_jsonl_records,
    read_no_follow_regular,
    require_boolean,
    require_identifier,
    require_posix_path,
    require_sha256,
    require_strict_keys,
    require_string,
    require_u64,
)
from turnvector_benchmark.core import ContractError

MAX_BYTES = 64 * 1024


def _line(obj):
    return canonical_jsonl_line(obj)


class ReadNoFollowRegularTests(unittest.TestCase):

    def _read(self, path):
        return read_no_follow_regular(path, MAX_BYTES, "test input")

    def test_regular_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.bin"
            payload = b"hello\x00world"
            path.write_bytes(payload)
            self.assertEqual(self._read(path), payload)

    def test_oversize_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.bin"
            path.write_bytes(b"x" * (MAX_BYTES + 1))
            with self.assertRaisesRegex(ContractError, "exceeds"):
                self._read(path)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target.bin"
            target.write_bytes(b"data")
            link = base / "link.bin"
            os.symlink(target, link)
            with self.assertRaisesRegex(ContractError, "symlink"):
                self._read(link)

    def test_dangling_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "link.bin"
            os.symlink(Path(directory) / "missing.bin", link)
            with self.assertRaisesRegex(ContractError, "symlink"):
                self._read(link)

    def test_directory_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.bin"
            path.mkdir()
            with self.assertRaisesRegex(ContractError, "regular file"):
                self._read(path)

    def test_fifo_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.bin"
            os.mkfifo(path)
            with self.assertRaisesRegex(ContractError, "regular file"):
                self._read(path)

    def test_truncation_during_read_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.bin"
            path.write_bytes(b"data")
            real = path.lstat()
            changed = os.stat_result(
                (
                    real.st_mode,
                    real.st_ino,
                    real.st_dev,
                    real.st_nlink,
                    real.st_uid,
                    real.st_gid,
                    real.st_size + 1,
                    real.st_atime,
                    real.st_mtime,
                    real.st_ctime,
                )
            )
            with mock.patch(
                "turnvector_benchmark.canonical.os.fstat",
                side_effect=[real, changed],
            ):
                with self.assertRaisesRegex(ContractError, "changed while reading"):
                    self._read(path)

    def test_os_error_from_read_is_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.bin"
            path.write_bytes(b"data")
            with mock.patch(
                "turnvector_benchmark.canonical.os.read",
                side_effect=OSError("boom"),
            ):
                with self.assertRaisesRegex(ContractError, "cannot read"):
                    self._read(path)


class ParseCanonicalJsonlTests(unittest.TestCase):

    def test_valid_records(self):
        raw = _line({"kind": "catalog", "a": 1}) + _line({"kind": "obligation", "b": 2})
        records = parse_canonical_jsonl_records(raw, "catalog")
        self.assertEqual(records[0], {"a": 1, "kind": "catalog"})
        self.assertEqual(records[1], {"b": 2, "kind": "obligation"})

    def test_lexical_keys_required(self):
        raw = b'{"kind":"catalog","z":1,"a":2}\n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_compact_required(self):
        raw = b'{"kind": "catalog"}\n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_ascii_escaping_required(self):
        raw = '{"kind":"caf\u00e9"}\n'.encode("utf-8")
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")
        escaped = b'{"kind":"caf\\u00e9"}\n'
        self.assertEqual(
            parse_canonical_jsonl_records(escaped, "catalog")[0], {"kind": "caf\u00e9"}
        )

    def test_noncanonical_escape_rejected(self):
        # "\u0061" for "a" is not canonical; canonical is the bare ASCII char.
        raw = b'{"kind":"\\u0061"}\n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_uppercase_hex_escape_rejected(self):
        raw = b'{"kind":"\\u00E9"}\n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_lone_surrogate_escape_rejected(self):
        raw = b'{"kind":"\\ud800"}\n'
        with self.assertRaisesRegex(ContractError, "surrogate"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_lone_surrogate_in_object_key_rejected(self):
        # Unpaired surrogate escapes in parsed object keys are as noncanonical
        # as in values and must be rejected by the parser.
        for raw in (b'{"\\ud800":"v"}\n', b'{"a\\udfff":"v"}\n'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ContractError, "surrogate"):
                    parse_canonical_jsonl_records(raw, "catalog")

    def test_lone_surrogate_in_nested_object_key_rejected(self):
        for raw in (
            b'{"kind":"catalog","n":{"\\ud800":"v"}}\n',
            b'{"kind":"catalog","n":[{"a\\udfff":"v"}]}\n',
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ContractError, "surrogate"):
                    parse_canonical_jsonl_records(raw, "catalog")

    def test_paired_surrogate_escape_in_object_key_accepted(self):
        # A valid surrogate pair combines into one non-surrogate character, so
        # only unpaired surrogate escapes are rejected.
        raw = '{"\\ud83d\\ude00":"v"}\n'.encode("ascii")
        records = parse_canonical_jsonl_records(raw, "catalog")
        self.assertEqual(records[0], {"\U0001f600": "v"})

    def test_escaped_slash_rejected(self):
        raw = b'{"kind":"a\\/b"}\n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_bom_rejected(self):
        raw = b"\xef\xbb\xbf" + _line({"kind": "catalog"})
        with self.assertRaisesRegex(ContractError, "BOM"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_crlf_rejected(self):
        raw = b'{"kind":"catalog"}\r\n'
        with self.assertRaisesRegex(ContractError, "LF"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_missing_final_lf_rejected(self):
        with self.assertRaisesRegex(ContractError, "final LF"):
            parse_canonical_jsonl_records(b'{"kind":"catalog"}', "catalog")

    def test_trailing_blank_line_rejected(self):
        raw = _line({"kind": "catalog"}) + b"\n"
        with self.assertRaisesRegex(ContractError, "blank"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_blank_line_in_middle_rejected(self):
        raw = _line({"kind": "catalog"}) + b"\n" + _line({"kind": "obligation"})
        with self.assertRaisesRegex(ContractError, "blank"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_empty_input_rejected(self):
        with self.assertRaises(ContractError):
            parse_canonical_jsonl_records(b"", "catalog")

    def test_invalid_utf8_rejected(self):
        with self.assertRaisesRegex(ContractError, "UTF-8"):
            parse_canonical_jsonl_records(b'\xff\xfe{"kind":"catalog"}\n', "catalog")

    def test_non_object_line_rejected(self):
        for raw in (b'["a"]\n', b'"x"\n', b"42\n", b"null\n", b"true\n"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ContractError, "object"):
                    parse_canonical_jsonl_records(raw, "catalog")

    def test_duplicate_key_rejected(self):
        raw = b'{"kind":"catalog","kind":"catalog"}\n'
        with self.assertRaisesRegex(ContractError, "duplicate"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_nested_duplicate_key_rejected(self):
        raw = b'{"kind":"catalog","n":{"a":1,"a":2}}\n'
        with self.assertRaisesRegex(ContractError, "duplicate"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_float_rejected(self):
        for raw in (b'{"kind":1.0}\n', b'{"kind":1e2}\n', b'{"kind":0.0}\n'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ContractError, "float"):
                    parse_canonical_jsonl_records(raw, "catalog")

    def test_nan_infinity_rejected(self):
        for raw in (b'{"kind":NaN}\n', b'{"kind":Infinity}\n', b'{"kind":-Infinity}\n'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ContractError, "constant"):
                    parse_canonical_jsonl_records(raw, "catalog")

    def test_leading_whitespace_rejected(self):
        raw = b' {"kind":"catalog"}\n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_trailing_whitespace_rejected(self):
        raw = b'{"kind":"catalog"} \n'
        with self.assertRaisesRegex(ContractError, "canonical"):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_record_limit_exact(self):
        raw = _line({"kind": "a"}) + _line({"kind": "b"})
        records = parse_canonical_jsonl_records(raw, "catalog", record_limit=2)
        self.assertEqual(len(records), 2)

    def test_record_limit_one_past_rejected_during_iteration(self):
        raw = _line({"kind": "a"}) + _line({"kind": "b"}) + _line({"kind": "c"})
        with self.assertRaisesRegex(ContractError, "record bound"):
            parse_canonical_jsonl_records(raw, "catalog", record_limit=2)

    def test_deeply_nested_input_is_bounded_contract_error(self):
        # Attacker-shaped deep nesting must not escape as RecursionError.
        depth = 200_000
        raw = b'{"kind":"catalog","n":' + b"[" * depth + b"]" * depth + b"}\n"
        with self.assertRaises(ContractError):
            parse_canonical_jsonl_records(raw, "catalog")

    def test_deeply_nested_parseable_input_validated_iteratively(self):
        # json.loads succeeds at this depth; the iterative validator and the
        # canonical re-encode must not overflow the interpreter stack.
        depth = 500
        raw = b'{"kind":"catalog","n":' + b"[" * depth + b"]" * depth + b"}\n"
        records = parse_canonical_jsonl_records(raw, "catalog")
        self.assertEqual(len(records), 1)


class CanonicalJsonlLineTests(unittest.TestCase):

    def test_canonical_roundtrip(self):
        value = {"z": [1, 2], "a": {"b": "caf\u00e9"}, "kind": "catalog"}
        raw = canonical_jsonl_line(value)
        self.assertEqual(raw, b'{"a":{"b":"caf\\u00e9"},"kind":"catalog","z":[1,2]}\n')
        records = parse_canonical_jsonl_records(raw, "catalog")
        self.assertEqual(records[0], value)

    def test_float_rejected(self):
        for bad in ({"kind": 1.0}, {"kind": "catalog", "n": [1.5]}, {"kind": 0.0}):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ContractError, "float"):
                    canonical_jsonl_line(bad)

    def test_nan_infinity_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ContractError, "float"):
                    canonical_jsonl_line({"kind": bad})

    def test_unpaired_surrogate_rejected(self):
        with self.assertRaisesRegex(ContractError, "surrogate"):
            canonical_jsonl_line({"kind": "\ud800"})
        with self.assertRaisesRegex(ContractError, "surrogate"):
            canonical_jsonl_line({"\udfff": "x"})
        with self.assertRaisesRegex(ContractError, "surrogate"):
            canonical_jsonl_line({"kind": "catalog", "n": {"x": "a\udc00b"}})

    def test_non_string_key_rejected(self):
        with self.assertRaisesRegex(ContractError, "non-string"):
            canonical_jsonl_line({1: "x"})
        with self.assertRaisesRegex(ContractError, "non-string"):
            canonical_jsonl_line({"kind": "catalog", "n": {None: 1}})
        with self.assertRaisesRegex(ContractError, "non-string"):
            canonical_jsonl_line({"kind": "catalog", "n": {(1, 2): "x"}})

    def test_non_json_value_rejected(self):
        with self.assertRaisesRegex(ContractError, "cannot be canonically encoded"):
            canonical_jsonl_line({"kind": ("a", "b")})
        with self.assertRaisesRegex(ContractError, "cannot be canonically encoded"):
            canonical_jsonl_line({"kind": {"x": {1, 2}}})

    def test_non_object_rejected(self):
        for bad in (["a"], "x", 42, None):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ContractError, "object"):
                    canonical_jsonl_line(bad)

    def test_deeply_nested_value_is_bounded_contract_error(self):
        value = {"kind": "catalog"}
        for _ in range(200_000):
            value = {"n": value}
        with self.assertRaises(ContractError):
            canonical_jsonl_line(value)

    def test_deeply_nested_flat_value_still_canonical(self):
        # Iterative validation must handle deep-but-encodable nesting.
        value = {"kind": "catalog"}
        for _ in range(200):
            value = {"n": value}
        raw = canonical_jsonl_line(value)
        records = parse_canonical_jsonl_records(raw, "catalog")
        self.assertEqual(records[0], value)


class TypedValidatorTests(unittest.TestCase):

    def test_require_boolean(self):
        self.assertIs(require_boolean(True, "x"), True)
        for bad in (1, 0, "true", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    require_boolean(bad, "x")

    def test_require_identifier(self):
        self.assertEqual(require_identifier("turnvector-implementation-v2", "x"), "turnvector-implementation-v2")
        for bad in ("", "No", "-x", "x y", "x/y", "1!", "x$"):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    require_identifier(bad, "x")

    def test_require_sha256(self):
        digest = "0" * 64
        self.assertEqual(require_sha256(digest, "x"), digest)
        for bad in ("0" * 63, "A" * 64, "", "0" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    require_sha256(bad, "x")

    def test_require_u64(self):
        self.assertEqual(require_u64(0, "x"), 0)
        self.assertEqual(require_u64((1 << 64) - 1, "x"), (1 << 64) - 1)
        for bad in (-1, 1 << 64, True, 1.5, "8"):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    require_u64(bad, "x")

    def test_require_string_byte_bound(self):
        self.assertEqual(require_string("ab", "x", max_bytes=2), "ab")
        with self.assertRaisesRegex(ContractError, "UTF-8 bytes"):
            require_string("abc", "x", max_bytes=2)
        with self.assertRaises(ContractError):
            require_string("", "x")

    def test_require_posix_path(self):
        self.assertEqual(require_posix_path("docs/adr/0001.md", "x"), "docs/adr/0001.md")
        for bad in (
            "/etc/passwd",
            "../x",
            "a/../b",
            "a//b",
            "a/./b",
            "a/b/",
            "a\\b",
            "a//",
            "docs/adr x/x.md",
            "",
            "./a",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ContractError):
                    require_posix_path(bad, "x")

    def test_require_strict_keys(self):
        value = {"a": 1, "b": 2}
        require_strict_keys(value, ("a", "b"), "x")
        with self.assertRaisesRegex(ContractError, "missing"):
            require_strict_keys(value, ("a", "b", "c"), "x")
        with self.assertRaisesRegex(ContractError, "unknown"):
            require_strict_keys(value, ("a",), "x")


if __name__ == "__main__":
    unittest.main()
