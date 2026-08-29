from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.artifacts import (
    ARTIFACT_MANIFEST_NAME,
    CHECKSUMS_NAME,
    ArtifactSpec,
    build_artifact_manifest,
    canonical_manifest_bytes,
    checksums_bytes,
    load_artifact_manifest,
    validate_artifact_manifest,
    write_artifact_manifest,
    write_sha256s_from_manifest,
)


class ArtifactManifestTests(unittest.TestCase):
    @staticmethod
    def build_manifest(root, specs, **kwargs):
        return build_artifact_manifest(
            root, specs, campaign_id="campaign-v1", **kwargs
        )

    def root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def fixture(self):
        root = self.root()
        (root / "attempts.jsonl").write_bytes(b'{"attempt":1}\n')
        (root / "nested").mkdir()
        (root / "nested" / "report.json").write_bytes(b'{"status":"valid"}\n')
        specs = (
            ArtifactSpec(
                "attempts",
                "attempts.jsonl",
                "application/x-ndjson",
                "turnvector.benchmark.attempt.v1",
            ),
            ArtifactSpec(
                "report",
                "nested/report.json",
                "application/json",
                "turnvector.benchmark.report.v1",
            ),
            ArtifactSpec(
                "native-routes",
                "native_routes.jsonl",
                "application/x-ndjson",
                "turnvector.benchmark.native-route.v1",
                required=False,
            ),
        )
        return root, specs

    def test_exact_manifest_golden_checksums_and_create_only_writes(self) -> None:
        root, specs = self.fixture()
        manifest = self.build_manifest(root, specs)
        self.assertEqual(manifest["artifact_count"], 3)
        self.assertEqual(manifest["present_artifact_count"], 2)
        self.assertEqual(
            manifest["total_size_bytes"],
            len(b'{"attempt":1}\n') + len(b'{"status":"valid"}\n'),
        )
        self.assertEqual([item["ordinal"] for item in manifest["artifacts"]], [1, 2, 3])
        self.assertFalse(manifest["artifacts"][2]["present"])
        self.assertIsNone(manifest["artifacts"][2]["sha256"])
        expected = (
            f"{hashlib.sha256((root / 'attempts.jsonl').read_bytes()).hexdigest()}  attempts.jsonl\n"
            f"{hashlib.sha256((root / 'nested/report.json').read_bytes()).hexdigest()}  nested/report.json\n"
        ).encode("utf-8")
        self.assertEqual(checksums_bytes(manifest), expected)
        manifest_path = write_artifact_manifest(root, manifest)
        checksum_path = write_sha256s_from_manifest(root, manifest)
        self.assertEqual(manifest_path.name, ARTIFACT_MANIFEST_NAME)
        self.assertEqual(checksum_path.name, CHECKSUMS_NAME)
        self.assertEqual(checksum_path.read_bytes(), expected)
        self.assertEqual(manifest_path.read_bytes(), canonical_manifest_bytes(manifest))
        self.assertEqual(load_artifact_manifest(root), manifest)
        with self.assertRaisesRegex(ContractError, "create-only"):
            write_artifact_manifest(root, manifest)
        with self.assertRaisesRegex(ContractError, "create-only"):
            write_sha256s_from_manifest(root, manifest)

    def test_unknown_fields_size_digest_and_order_mismatch_fail_closed(self) -> None:
        root, specs = self.fixture()
        manifest = self.build_manifest(root, specs)
        mutations = []
        value = copy.deepcopy(manifest)
        value["unknown"] = True
        mutations.append(value)
        value = copy.deepcopy(manifest)
        value["artifacts"][0]["size_bytes"] += 1
        mutations.append(value)
        value = copy.deepcopy(manifest)
        value["artifacts"][0]["sha256"] = "0" * 64
        mutations.append(value)
        value = copy.deepcopy(manifest)
        value["artifacts"][1]["ordinal"] = 1
        mutations.append(value)
        value = copy.deepcopy(manifest)
        value["present_artifact_count"] = 3
        mutations.append(value)
        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    validate_artifact_manifest(root, value)

    def test_absolute_traversal_alias_and_reserved_paths_fail_closed(self) -> None:
        root = self.root()
        for path in ("/tmp/x", "../x", "nested/../x", "nested\\x", "a//b", "artifact_manifest.json", "SHA256SUMS"):
            with self.subTest(path=path):
                with self.assertRaises(ContractError):
                    self.build_manifest(
                        root,
                        (ArtifactSpec("artifact", path, "application/json", None, required=False),),
                    )
        (root / "a.json").write_text("a", encoding="utf-8")
        duplicate_id = (
            ArtifactSpec("same", "a.json", "application/json", None),
            ArtifactSpec("same", "missing.json", "application/json", None, required=False),
        )
        with self.assertRaisesRegex(ContractError, "duplicate artifact ID"):
            self.build_manifest(root, duplicate_id)

    def test_undeclared_files_and_byte_limits_fail_closed(self) -> None:
        root = self.root()
        (root / "declared.bin").write_bytes(b"1234")
        (root / "undeclared.bin").write_bytes(b"x")
        spec = ArtifactSpec("declared", "declared.bin", "application/octet-stream", None)
        with self.assertRaisesRegex(ContractError, "undeclared"):
            self.build_manifest(root, (spec,))
        (root / "undeclared.bin").unlink()
        with self.assertRaisesRegex(ContractError, "file limit"):
            self.build_manifest(root, (spec,), per_file_byte_limit=3)
        with self.assertRaisesRegex(ContractError, "total limit"):
            self.build_manifest(root, (spec,), total_byte_limit=3)

    def test_symlink_hardlink_special_file_and_symlink_root_fail_closed(self) -> None:
        root = self.root()
        target = root / "target.json"
        target.write_text("{}\n", encoding="utf-8")
        link = root / "link.json"
        link.symlink_to(target)
        with self.assertRaisesRegex(ContractError, "symlink"):
            self.build_manifest(
                root,
                (ArtifactSpec("link", "link.json", "application/json", None),),
            )
        link.unlink()
        hard = root / "hard.json"
        os.link(target, hard)
        with self.assertRaisesRegex(ContractError, "hard-link"):
            self.build_manifest(
                root,
                (ArtifactSpec("target", "target.json", "application/json", None),),
                reject_undeclared=False,
            )
        hard.unlink()
        if hasattr(os, "mkfifo"):
            fifo = root / "pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ContractError, "regular file"):
                self.build_manifest(
                    root,
                    (ArtifactSpec("pipe", "pipe", "application/octet-stream", None),),
                    reject_undeclared=False,
                )
            fifo.unlink()
        outside = self.root()
        root_link = outside / "root-link"
        root_link.symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(ContractError, "real directory"):
            self.build_manifest(
                root_link,
                (ArtifactSpec("target", "target.json", "application/json", None),),
            )

    def test_post_manifest_file_mutation_is_detected(self) -> None:
        root, specs = self.fixture()
        manifest = self.build_manifest(root, specs)
        (root / "attempts.jsonl").write_bytes(b'{"attempt":2}\n')
        with self.assertRaisesRegex(ContractError, "does not match"):
            validate_artifact_manifest(root, manifest)


if __name__ == "__main__":
    unittest.main()
