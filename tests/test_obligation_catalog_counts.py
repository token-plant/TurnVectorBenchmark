"""Tests for load_obligation_catalog: counts, order, canonical bytes, caps."""

import os
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.compile_limits import CompileLimits
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.obligation_catalog import load_obligation_catalog
from tests.obligation_catalog_test_utils import (
    CATALOG,
    expect_contract_error,
    expect_raw_contract_error,
    fixture_objects,
    optional_record,
    raw_variant,
    write_variant,
)


def _swap_first_two(objects):
    objects[1], objects[2] = objects[2], objects[1]


class CountAndOrderViolationTests(unittest.TestCase):

    def test_unsorted_ids_rejected(self):
        expect_contract_error(_swap_first_two, message="sorted")

    def test_duplicate_id_rejected(self):
        expect_contract_error(
            lambda objects: objects[2].update({"id": objects[1]["id"]}),
            message="sorted",
        )

    def test_obligation_id_collides_with_header(self):
        # Mutate the last record so id order stays sorted; the header-id
        # collision check must then fire.
        expect_contract_error(
            lambda objects: objects[-1].update({"id": objects[0]["id"]}),
            message="header id",
        )

    def test_duplicate_lane_behavior_pair_rejected(self):
        expect_contract_error(
            lambda objects: objects[2].update(
                {"behavior_case_id": objects[1]["behavior_case_id"]}
            ),
            message="unique",
        )

    def test_per_lane_count_mismatch_rejected(self):
        expect_contract_error(
            lambda objects: objects[1].update({"lane_id": "scheduler-policy"}),
            message="per-lane",
        )

    def test_too_few_required_rejected(self):
        expect_contract_error(
            lambda objects: objects[1].update(
                {"required": False, "readiness_status": "intentionally_out_of_scope", "blocker_ids": []}
            ),
            message="46",
        )

    def test_too_many_required_rejected(self):
        expect_contract_error(
            lambda objects: objects[-1].update(
                {"required": True, "readiness_status": "design_ready", "blocker_ids": []}
            ),
            message="46",
        )

    def test_obligation_count_max_exact_and_one_past(self):
        def build(with_optional_extra):
            objects = fixture_objects()
            # The fixture carries 47 obligation records (46 required + 1
            # optional); 209 added optional records reach exactly
            # obligation_count_max=256 obligations, 210 exceed it.
            count = 209 + (1 if with_optional_extra else 0)
            for n in range(48, 48 + count):
                objects.append(optional_record(n))
            objects[0]["record_count"] = len(objects)
            return objects

        with tempfile.TemporaryDirectory() as directory:
            path = write_variant(build(False), directory)
            catalog = load_obligation_catalog(path)
            self.assertEqual(len(catalog.obligations), 256)
        with tempfile.TemporaryDirectory() as directory:
            path = write_variant(build(True), directory)
            with self.assertRaisesRegex(ContractError, "obligation bound"):
                load_obligation_catalog(path)


class BoundaryAcceptanceTests(unittest.TestCase):

    def test_zero_optional_obligations_boundary_accepted(self):
        # O_opt = 0 boundary: exactly the 46 required obligations and no
        # optional records, so record_count = 1 header + 46 = 47. The fixture
        # stays untouched; the boundary variant is built in a temporary
        # directory by dropping its single optional record.
        objects = fixture_objects()
        optional = [obj for obj in objects[1:] if not obj["required"]]
        self.assertEqual(len(optional), 1)
        objects = [objects[0]] + [obj for obj in objects[1:] if obj["required"]]
        objects[0]["record_count"] = len(objects)
        with tempfile.TemporaryDirectory() as directory:
            catalog = load_obligation_catalog(write_variant(objects, directory))
        self.assertEqual(catalog.header.record_count, 47)
        self.assertEqual(len(catalog.obligations), 46)
        self.assertEqual(len(catalog.required_obligations), 46)
        # No optional records: every obligation is required.
        self.assertEqual(catalog.required_obligations, catalog.obligations)


class CanonicalEncodingViolationTests(unittest.TestCase):

    def test_raw_encoding_rejections(self):
        raw = CATALOG.read_bytes()
        cases = {
            "bom": b"\xef\xbb\xbf" + raw,
            "crlf": raw.replace(b"\n", b"\r\n", 1),
            "missing final lf": raw.rstrip(b"\n"),
            "trailing blank line": raw + b"\n",
            "blank line in middle": raw.replace(b"\n", b"\n\n", 1),
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                expect_raw_contract_error(payload)

    def test_duplicate_key_in_line_rejected(self):
        line = CATALOG.read_bytes().split(b"\n")[0]
        duplicated = line.replace(b'"id":', b'"id":"x","id":', 1)
        raw = duplicated + b"\n" + b"\n".join(CATALOG.read_bytes().split(b"\n")[1:])
        expect_raw_contract_error(raw)

    def test_float_in_line_rejected(self):
        # The canonical serializer rejects floats up front, so manufacture the
        # raw invalid bytes directly: a float section offset must be rejected
        # by the loader's strict parser.
        objects = fixture_objects()
        objects[1]["section_start"] = 1.0
        expect_raw_contract_error(raw_variant(objects))

    def test_record_count_max_injected_one_past_fails_during_parse(self):
        # A small injected catalog_record_count_max makes the one-past record
        # fail during streaming/iteration (before an unbounded decoded list).
        limits = CompileLimits(catalog_record_count_max=3)
        objects = fixture_objects()  # 48 records; record 3 is the one-past.
        with tempfile.TemporaryDirectory() as directory:
            path = write_variant(objects, directory)
            with self.assertRaisesRegex(ContractError, "record bound"):
                load_obligation_catalog(path, limits=limits)

    def test_noncanonical_escape_rejected(self):
        text = CATALOG.read_text(encoding="ascii")
        text = text.replace('"synthetic-grade"', '"synthetic\\u002dgrade"', 1)
        expect_raw_contract_error(text.encode("ascii"))

    def test_unsorted_line_reordering_rejected(self):
        lines = CATALOG.read_bytes().split(b"\n")
        header, rest = lines[0], [line for line in lines[1:] if line]
        rest[0], rest[1] = rest[1], rest[0]
        expect_raw_contract_error(header + b"\n" + b"\n".join(rest) + b"\n")

    def test_oversize_input_rejected(self):
        size = CompileLimits.frozen().largest_single_serialized_parser_input_max
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            path.write_bytes(b"x" * (size + 1))
            with self.assertRaisesRegex(ContractError, "exceeds"):
                load_obligation_catalog(path)


class LoadNoFollowTests(unittest.TestCase):

    def test_symlink_catalog_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target.jsonl"
            target.write_bytes(CATALOG.read_bytes())
            link = base / "catalog.jsonl"
            os.symlink(target, link)
            with self.assertRaisesRegex(ContractError, "symlink"):
                load_obligation_catalog(link)

    def test_directory_catalog_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            path.mkdir()
            with self.assertRaisesRegex(ContractError, "regular file"):
                load_obligation_catalog(path)


if __name__ == "__main__":
    unittest.main()
