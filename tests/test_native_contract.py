from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.lane_runner import (
    CPP_DIRECT_BUNDLE_SCHEMA,
    _load_cpp_direct_bundle,
    _read_latency_csv,
)


ROOT = Path(__file__).resolve().parent.parent


class NativeContractTests(unittest.TestCase):
    def make_bundle(self, root: Path) -> Path:
        (root / "bin").mkdir()
        (root / "graphs").mkdir()
        (root / "bin" / "oracle").write_bytes(b"fixture-binary")
        (root / "graphs" / "dense.mlxfn").write_bytes(b"dense")
        (root / "graphs" / "moe.mlxfn").write_bytes(b"moe")
        manifest = {
            "schema_version": CPP_DIRECT_BUNDLE_SCHEMA,
            "binary": "bin/oracle",
            "seed": 20260812,
            "warmup": 2,
            "iterations": 3,
            "source_revisions": {
                "mlx": "68cf2fddd8de5edd8ab3d926391772b2e2cedad8",
                "mlx-c": "fba4470b89073180056c9ea46c443051375f7399",
            },
            "models": {
                "dense": {
                    "graph": "graphs/dense.mlxfn",
                    "layers": 28,
                    "kv_heads": 8,
                    "head_dim": 128,
                    "vocab_size": 151936,
                },
                "moe": {
                    "graph": "graphs/moe.mlxfn",
                    "layers": 24,
                    "kv_heads": 8,
                    "head_dim": 128,
                    "vocab_size": 151936,
                },
            },
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_cpp_direct_bundle_is_strict_and_lock_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.make_bundle(root)
            descriptor = {
                "kind": "directory",
                "path": str(root),
                "sha256": "0" * 64,
            }
            bundle = _load_cpp_direct_bundle(descriptor, ROOT)
            self.assertEqual(bundle["iterations"], 3)
            self.assertEqual(bundle["models"]["dense"]["vocab_size"], 151936)

            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["source_revisions"]["mlx-c"] = "f" * 40
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "reference lock"):
                _load_cpp_direct_bundle(descriptor, ROOT)

    def test_native_latency_csv_requires_complete_positive_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latency.csv"
            path.write_text(
                "iteration,wall_us,engine_service_us\n"
                "0,12.5,10.0\n"
                "1,13.0,10.5\n",
                encoding="ascii",
            )
            values = _read_latency_csv(
                path,
                ("iteration", "wall_us", "engine_service_us"),
                "candidate latency",
            )
            self.assertEqual(len(values), 2)

            path.write_text(
                "iteration,wall_us,engine_service_us\n0,12.5,0\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(ContractError, "positive and finite"):
                _read_latency_csv(
                    path,
                    ("iteration", "wall_us", "engine_service_us"),
                    "candidate latency",
                )


if __name__ == "__main__":
    unittest.main()
