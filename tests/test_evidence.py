from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.evidence import write_checksums
from turnvector_benchmark.external import _directory_identity, load_external_fixture_manifest


ROOT = Path(__file__).resolve().parent.parent
REFERENCE_LOCK = ROOT / "oracles" / "mlx" / "reference-lock-v1.json"


class EvidenceTests(unittest.TestCase):
    def test_checksums_support_utf8_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "cases" / "uber.bin"
            artifact.parent.mkdir()
            artifact.write_bytes(b"evidence")
            artifact.rename(artifact.with_name("uber-ü.bin"))

            checksums = write_checksums(root)

            text = checksums.read_text(encoding="utf-8")
            self.assertIn("cases/uber-ü.bin", text)

    def test_external_file_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.bin"
            target.write_bytes(b"fixture")
            link = root / "fixture.bin"
            link.symlink_to(target)
            manifest = {
                "schema_version": "turnvector.benchmark.external-fixtures.v1",
                "id": "test-fixtures",
                "reference_lock_sha256": hashlib.sha256(
                    REFERENCE_LOCK.read_bytes()
                ).hexdigest(),
                "artifacts": [
                    {
                        "id": "dense-model",
                        "path": str(link),
                        "kind": "file",
                        "size": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                ],
            }
            manifest_path = root / "external.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ContractError, "symlink"):
                load_external_fixture_manifest(
                    manifest_path, reference_lock=REFERENCE_LOCK
                )

    def test_directory_identity_includes_executable_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "oracle"
            binary.write_bytes(b"fixture-binary")
            os.chmod(binary, 0o644)
            before = _directory_identity(root)

            os.chmod(binary, 0o755)
            after = _directory_identity(root)

            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
