"""Tests for verify_obligation_sources: range/hash, no-follow, races, caps."""

import hashlib
import os
import resource
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import turnvector_benchmark.obligation_sources as sources_module
from turnvector_benchmark.canonical import canonical_jsonl_line
from turnvector_benchmark.compile_limits import CompileLimits
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.obligation_catalog import load_obligation_catalog
from turnvector_benchmark.obligation_sources import verify_obligation_sources
from tests.obligation_catalog_test_utils import (
    CATALOG,
    SOURCE_ROOT,
    fixture_objects,
)

LIMITS = CompileLimits.frozen()


def _canonical_base(directory):
    """Resolve the canonical (symlink-free) base of a temp directory.

    The source-root contract requires an absolute canonical root with no
    symlinked ancestors, but macOS ``/var`` is a symlink to ``/private/var``,
    so ``tempfile`` paths are not canonical as returned.
    """
    return Path(os.path.realpath(directory))


def _write_catalog(directory, objects):
    path = Path(directory) / "catalog.jsonl"
    path.write_bytes(b"".join(canonical_jsonl_line(obj) for obj in objects))
    return load_obligation_catalog(path)


def _write_sources(source_root, files):
    for rel, content in files.items():
        path = source_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _rebuild_catalog(directory, source_root, files, plan):
    """Rewrite every obligation citation to *plan* over *files*.

    *plan* is a list of ``(rel_path, start, end)`` with one entry per
    obligation record (47 entries), and *files* maps rel_path to bytes.
    """
    _write_sources(source_root, files)
    objects = fixture_objects()
    digests = {rel: hashlib.sha256(content).hexdigest() for rel, content in files.items()}
    for obj, (rel, start, end) in zip(objects[1:], plan):
        obj["source_path"] = rel
        obj["source_file_sha256"] = digests[rel]
        obj["section_start"] = start
        obj["section_end"] = end
        obj["section_sha256"] = hashlib.sha256(files[rel][start:end]).hexdigest()
    return _write_catalog(directory, objects)


def _one_file_catalog(directory, source_root, content=b"source content\n", section=(0, 8)):
    files = {"single.md": content}
    return _rebuild_catalog(directory, source_root, files, [("single.md",) + section] * 47)


def _expect_reject(catalog, source_root, message=None):
    try:
        verify_obligation_sources(catalog, source_root)
    except ContractError as error:
        if message is not None and message not in str(error):
            raise AssertionError(
                f"expected message containing {message!r}, got {error!r}"
            ) from error
        return
    raise AssertionError("expected ContractError")


class VerifyCommittedFixtureTests(unittest.TestCase):

    def test_fixture_verifies(self):
        catalog = load_obligation_catalog(CATALOG)
        result = verify_obligation_sources(catalog, SOURCE_ROOT)
        distinct = {record.source_path for record in catalog.obligations}
        self.assertEqual(result.path_count, len(distinct))
        self.assertEqual(result.section_count, len(catalog.obligations))
        self.assertEqual(
            result.section_bytes_total,
            sum(record.section_end - record.section_start for record in catalog.obligations),
        )
        self.assertEqual(
            result.source_bytes_total,
            sum((SOURCE_ROOT / path).stat().st_size for path in distinct),
        )

    def test_every_cited_file_and_range_is_checked(self):
        catalog = load_obligation_catalog(CATALOG)
        for record in catalog.obligations:
            content = (SOURCE_ROOT / record.source_path).read_bytes()
            self.assertEqual(
                hashlib.sha256(content).hexdigest(), record.source_file_sha256
            )
            self.assertLessEqual(record.section_end, len(content))
            self.assertEqual(
                hashlib.sha256(content[record.section_start : record.section_end]).hexdigest(),
                record.section_sha256,
            )


class MissingSourceTests(unittest.TestCase):

    def test_empty_root_rejected(self):
        catalog = load_obligation_catalog(CATALOG)
        with tempfile.TemporaryDirectory() as directory:
            _expect_reject(catalog, _canonical_base(directory))

    def test_missing_source_root_rejected(self):
        catalog = load_obligation_catalog(CATALOG)
        with tempfile.TemporaryDirectory() as directory:
            _expect_reject(catalog, _canonical_base(directory) / "missing")

    def test_root_must_be_directory(self):
        catalog = load_obligation_catalog(CATALOG)
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "file.txt"
            root.write_bytes(b"x")
            _expect_reject(catalog, root, message="not a directory")


class DigestMismatchTests(unittest.TestCase):

    @staticmethod
    def _single_objects(content, section=(0, 8)):
        objects = fixture_objects()
        digest = hashlib.sha256(content).hexdigest()
        for obj in objects[1:]:
            obj["source_path"] = "single.md"
            obj["source_file_sha256"] = digest
            obj["section_start"], obj["section_end"] = section
            obj["section_sha256"] = hashlib.sha256(content[section[0] : section[1]]).hexdigest()
        return objects

    def test_corrupted_source_file_byte_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            _write_sources(root, {"single.md": content})
            catalog = _write_catalog(directory, self._single_objects(content))
            corrupted = bytes([content[0] ^ 1]) + content[1:]
            (root / "single.md").write_bytes(corrupted)
            _expect_reject(catalog, root, message="source_file_sha256")

    def test_catalog_file_digest_mismatch_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            _write_sources(root, {"single.md": content})
            objects = self._single_objects(content)
            objects[1]["source_file_sha256"] = "0" * 64
            catalog = _write_catalog(directory, objects)
            _expect_reject(catalog, root, message="source_file_sha256")

    def test_catalog_section_digest_mismatch_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            _write_sources(root, {"single.md": content})
            objects = self._single_objects(content)
            objects[1]["section_sha256"] = "0" * 64
            catalog = _write_catalog(directory, objects)
            _expect_reject(catalog, root, message="section digest mismatch")


class RangeValidationTests(unittest.TestCase):

    def test_section_end_equal_file_length_accepted(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, len(content)))
            result = verify_obligation_sources(catalog, root)
            self.assertEqual(result.section_count, 47)

    def test_section_end_one_past_file_length_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, len(content) + 1))
            _expect_reject(catalog, root, message="exceeds the source file length")

    def test_section_end_beyond_file_length_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 4096))
            _expect_reject(catalog, root, message="exceeds the source file length")

    def test_reversed_range_rejected_at_load(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            with self.assertRaises(ContractError):
                _one_file_catalog(directory, root, content=content, section=(8, 2))


class SymlinkTraversalTests(unittest.TestCase):

    def _catalog(self, directory, root):
        content = b"0123456789abcdef"
        return _one_file_catalog(directory, root, content=content, section=(0, 8))

    def test_symlinked_source_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            real = root / "real.md"
            real.write_bytes(b"0123456789abcdef")
            os.symlink(real, root / "single.md")
            catalog = self._catalog(directory, root)
            _expect_reject(catalog, root, message="symlink")

    def test_symlinked_intermediate_directory_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            real_dir = _canonical_base(directory) / "real_dir"
            real_dir.mkdir()
            (real_dir / "single.md").write_bytes(b"0123456789abcdef")
            os.symlink(real_dir, root)
            catalog = load_obligation_catalog(CATALOG)
            _expect_reject(catalog, root, message="symlink")

    def test_symlinked_root_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            real = _canonical_base(directory) / "real"
            real.mkdir()
            (real / "single.md").write_bytes(b"0123456789abcdef")
            link = _canonical_base(directory) / "link"
            os.symlink(real, link)
            catalog = load_obligation_catalog(CATALOG)
            _expect_reject(catalog, link, message="root")

    def test_nested_path_within_root_accepted(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            plan = [("docs/adr/0001.md", 0, 8)] * 47
            files = {"docs/adr/0001.md": content}
            catalog = _rebuild_catalog(directory, root, files, plan)
            result = verify_obligation_sources(catalog, root)
            self.assertEqual(result.path_count, 1)


class RootValidationTests(unittest.TestCase):
    """The source root must be absolute, canonical, and symlink-free."""

    def _catalog(self, directory, root):
        content = b"0123456789abcdef"
        return _one_file_catalog(directory, root, content=content, section=(0, 8))

    def test_relative_root_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = self._catalog(directory, root)
            _expect_reject(catalog, "relative/source", message="absolute path")

    def test_noncanonical_root_strings_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = _canonical_base(directory)
            root = base / "source"
            root.mkdir()
            catalog = self._catalog(directory, root)
            text = str(root)
            for bad in (
                text + "/..",
                text + "/.",
                text + "/",
                "//" + text.lstrip("/"),
            ):
                with self.subTest(bad=bad):
                    _expect_reject(catalog, bad, message="canonical")

    def test_symlinked_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = _canonical_base(directory)
            real = base / "real"
            (real / "source").mkdir(parents=True)
            (real / "source" / "single.md").write_bytes(b"0123456789abcdef")
            link = base / "link"
            os.symlink(real, link)
            catalog = self._catalog(directory, real / "source")
            _expect_reject(catalog, link / "source", message="symlink")

    def test_root_identity_bound_and_returned(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 8))
            result = verify_obligation_sources(catalog, root)
            entry = root.stat()
            self.assertEqual(result.root_path, os.fspath(root))
            self.assertEqual(result.root_device, entry.st_dev)
            self.assertEqual(result.root_inode, entry.st_ino)
            self.assertEqual(result.root_uid, entry.st_uid)
            self.assertEqual(result.root_gid, entry.st_gid)
            self.assertEqual(result.root_mode, entry.st_mode)


class RootIdentityTests(unittest.TestCase):
    """The held root identity is revalidated at the end of the pass."""

    def test_root_replaced_between_whole_file_and_section_pass_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            base = _canonical_base(directory)
            root = base / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 8))
            real_hash_range = sources_module._hash_range_fd
            replaced = {"done": False}

            def replace_then_hash(fd, start, end, expected, hash_buffer, what):
                if not replaced["done"]:
                    replaced["done"] = True
                    # Swap the root directory behind the held descriptor: the
                    # whole-file pass hashed the original; the section pass
                    # still reads through the held fd; only the end-of-pass
                    # path revalidation can detect the replacement.
                    os.rename(root, base / "source.bak")
                    new_root = base / "source"
                    new_root.mkdir()
                    (new_root / "single.md").write_bytes(content)
                return real_hash_range(fd, start, end, expected, hash_buffer, what)

            with mock.patch(
                "turnvector_benchmark.obligation_sources._hash_range_fd",
                side_effect=replace_then_hash,
            ):
                _expect_reject(catalog, root, message="changed during verification")

    def test_root_mode_change_during_pass_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            base = _canonical_base(directory)
            root = base / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 8))
            real_hash_range = sources_module._hash_range_fd
            original_mode = root.stat().st_mode
            changed = {"done": False}

            def chmod_then_hash(fd, start, end, expected, hash_buffer, what):
                if not changed["done"]:
                    changed["done"] = True
                    # Toggle the directory's user-write bit only: it changes
                    # the bound mode without blocking traversal.
                    os.chmod(root, original_mode ^ 0o200)
                return real_hash_range(fd, start, end, expected, hash_buffer, what)

            with mock.patch(
                "turnvector_benchmark.obligation_sources._hash_range_fd",
                side_effect=chmod_then_hash,
            ):
                _expect_reject(catalog, root, message="changed during verification")


class DeepPathTests(unittest.TestCase):
    """Deep many-component paths must not exhaust the descriptor table."""

    def test_deep_many_component_path_without_fd_exhaustion(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            base = _canonical_base(directory)
            root = base / "source"
            root.mkdir()
            # ~300 single-character components stay well under the 4096-byte
            # per-path cap while far exceeding any plausible O(depth) fd
            # budget. Descent closes each intermediate after its child opens,
            # so the whole pass needs only a handful of descriptors.
            depth = 300
            rel = "/".join(["a"] * depth) + "/f.md"
            self.assertLess(
                len(rel.encode("utf-8")), LIMITS.execution_closure_path_bytes_max
            )
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            plan = [(rel, 0, 8)] * 47
            catalog = _rebuild_catalog(directory, root, {rel: content}, plan)
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (32, hard))
                result = verify_obligation_sources(catalog, root)
            finally:
                resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
            self.assertEqual(result.path_count, 1)
            self.assertEqual(result.section_count, 47)


class RaceTests(unittest.TestCase):

    def _stat_changed(self, entry, size_delta=1):
        # Same device/inode as *entry* (so identity checks on the wrong fd
        # never fire); only the size differs.
        return os.stat_result(
            (
                entry.st_mode,
                entry.st_ino,
                entry.st_dev,
                entry.st_nlink,
                entry.st_uid,
                entry.st_gid,
                entry.st_size + size_delta,
                entry.st_atime,
                entry.st_mtime,
                entry.st_ctime,
            )
        )

    def _patched_file_fstat(self, file_entry, at_call, size_delta=1):
        """Return a fake os.fstat that mutates only the cited source file.

        Directory fstats keep their real identity, so the call index counts
        only fstats of the file itself (open post-check, hash before/after,
        section open/range before/after), independent of the root path depth.
        """
        calls = {"count": 0}
        original_fstat = os.fstat

        def fake_fstat(fd):
            result = original_fstat(fd)
            if (result.st_dev, result.st_ino) == (file_entry.st_dev, file_entry.st_ino):
                calls["count"] += 1
                if calls["count"] == at_call:
                    return self._stat_changed(result, size_delta)
            return result

        return fake_fstat

    def test_change_during_file_hash_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 8))
            real = (root / "single.md").lstat()
            # File fstat calls: 1 = open post-check, 2 = hash before,
            # 3 = hash after (the mutated one).
            fake = self._patched_file_fstat(real, at_call=3)
            with mock.patch("turnvector_benchmark.obligation_sources.os.fstat", side_effect=fake):
                _expect_reject(catalog, root, message="changed while reading")

    def test_change_between_file_hash_and_section_rejected(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 8))
            real = (root / "single.md").lstat()
            # File fstat calls: 4 = section-open post-check, 5 = range before
            # (the mutated one); the whole-file hash-after (call 3) stays real.
            fake = self._patched_file_fstat(real, at_call=5)
            with mock.patch("turnvector_benchmark.obligation_sources.os.fstat", side_effect=fake):
                _expect_reject(catalog, root, message="changed between verification passes")

    def test_read_error_is_contract_error(self):
        content = b"0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            root = _canonical_base(directory) / "source"
            root.mkdir()
            catalog = _one_file_catalog(directory, root, content=content, section=(0, 8))
            with mock.patch(
                "turnvector_benchmark.obligation_sources.os.read",
                side_effect=OSError("boom"),
            ):
                _expect_reject(catalog, root, message="cannot read")


