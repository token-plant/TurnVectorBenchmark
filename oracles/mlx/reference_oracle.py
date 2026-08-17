#!/usr/bin/env python3
"""Pinned full-model MLX-LM oracle for output, complete logits, and layer KV."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import struct
from pathlib import Path
from typing import Any, BinaryIO, Iterable, List, Mapping

import mlx.core as mx
import numpy as np
from mlx_lm import load
from mlx_lm.models.cache import make_prompt_cache


SEED = 20260812
EXPECTED_PACKAGES = {"mlx": "0.31.2", "mlx-lm": "0.31.3"}
DECODE_CONTEXTS = {512, 2048, 8192}
PREFILL_TOKENS = {64, 256, 1024}
BATCHES = {1, 4}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_environment() -> None:
    for package, expected in EXPECTED_PACKAGES.items():
        observed = importlib.metadata.version(package)
        if observed != expected:
            raise RuntimeError(
                f"qualification requires {package}=={expected}; observed {observed}"
            )


def deterministic_tokens(batch: int, length: int, vocab_size: int, seed: int) -> mx.array:
    if vocab_size <= 1024:
        raise RuntimeError("model vocabulary is too small for deterministic token generation")
    values = [
        ((seed + row * 104729 + column * 8191) % (vocab_size - 1024)) + 1024
        for row in range(batch)
        for column in range(length)
    ]
    return mx.array(values, dtype=mx.int32).reshape(batch, length)


def cache_arrays(cache: Iterable[Any]) -> List[mx.array]:
    arrays: List[mx.array] = []
    for layer in cache:
        if layer.empty():
            raise RuntimeError("model returned an empty layer cache after execution")
        state = layer.state
        if not isinstance(state, tuple) or len(state) != 2:
            raise RuntimeError("qualification requires one key/value state pair per layer")
        arrays.extend(state)
    return arrays


def normalized_array(value: mx.array) -> np.ndarray:
    fp32 = value.astype(mx.float32)
    mx.eval(fp32)
    return np.asarray(fp32, dtype="<f4", order="C")


def write_tensor(stream: BinaryIO, name: str, value: mx.array) -> Mapping[str, Any]:
    normalized = normalized_array(value)
    raw = normalized.tobytes(order="C")
    metadata = {
        "name": name,
        "dtype": "float32-le",
        "shape": list(normalized.shape),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    header = canonical_json(metadata).encode("ascii")
    stream.write(struct.pack("<I", len(header)))
    stream.write(header)
    stream.write(raw)
    return metadata


def write_output_tokens(path: Path, logits: mx.array) -> Mapping[str, Any]:
    token_ids = np.asarray(mx.argmax(logits, axis=-1), dtype="<i4", order="C")
    mx.eval(logits)
    path.write_bytes(token_ids.tobytes(order="C"))
    return {
        "path": path.name,
        "dtype": "int32-le",
        "shape": list(token_ids.shape),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def write_logits(path: Path, logits: mx.array) -> Mapping[str, Any]:
    with path.open("wb") as stream:
        tensor = write_tensor(stream, "complete_logits", logits)
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256(path), "tensor": tensor}


def write_kv(path: Path, arrays: List[mx.array]) -> Mapping[str, Any]:
    tensors = []
    with path.open("wb") as stream:
        for index in range(0, len(arrays), 2):
            layer = index // 2
            tensors.append(write_tensor(stream, f"layer-{layer:04d}-key", arrays[index]))
            tensors.append(write_tensor(stream, f"layer-{layer:04d}-value", arrays[index + 1]))
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "tensor_count": len(tensors),
        "tensors": tensors,
    }


def run_case(args: argparse.Namespace) -> Mapping[str, Any]:
    validate_environment()
    if args.phase == "decode":
        if args.shape not in DECODE_CONTEXTS or args.batch not in BATCHES:
            raise RuntimeError("decode case is outside the locked qualification matrix")
    elif args.shape not in PREFILL_TOKENS or args.batch != 1:
        raise RuntimeError("prefill case is outside the locked qualification matrix")
    model, _ = load(args.model, lazy=True)
    mx.eval(model.parameters())
    cache = make_prompt_cache(model)
    prefill_generated = False
    if args.phase == "decode":
        prefix = deterministic_tokens(args.batch, args.shape, model.args.vocab_size, SEED)
        prefix_logits = model(prefix, cache=cache)[:, -1, :]
        mx.eval(prefix_logits, *cache_arrays(cache))
        prefill_generated = True
        if args.synthetic_zero_kv:
            for layer in cache:
                layer.state = tuple(mx.zeros_like(value) for value in layer.state)
            mx.eval(*cache_arrays(cache))
        else:
            nonzero = [mx.any(value != 0) for value in cache_arrays(cache)]
            mx.eval(*nonzero)
            if not any(bool(value.item()) for value in nonzero):
                raise RuntimeError("decode qualification KV remained zero after Prefill")
        tokens = deterministic_tokens(args.batch, 1, model.args.vocab_size, SEED + 17)
    else:
        tokens = deterministic_tokens(args.batch, args.shape, model.args.vocab_size, SEED)
    logits = model(tokens, cache=cache)[:, -1, :].astype(mx.float32)
    arrays = cache_arrays(cache)
    mx.eval(logits, *arrays)
    args.output.mkdir(parents=True, exist_ok=False)
    output_record = write_output_tokens(args.output / "output-tokens.i32", logits)
    logits_record = write_logits(args.output / "complete-logits.bin", logits)
    kv_record = write_kv(args.output / "layer-kv-normalized.bin", arrays)
    manifest = {
        "schema_version": "turnvector.benchmark.mlx-oracle-evidence.v1",
        "oracle": "python-mlx-lm-full-model",
        "model_architecture": args.model_architecture,
        "model_path": str(args.model.resolve()),
        "phase": args.phase,
        "batch": args.batch,
        "shape": args.shape,
        "seed": SEED,
        "prefill_generated_kv": prefill_generated,
        "synthetic_zero_kv": bool(args.synthetic_zero_kv),
        "qualification_eligible": not args.synthetic_zero_kv,
        "packages": EXPECTED_PACKAGES,
        "output": output_record,
        "logits": logits_record,
        "kv": kv_record,
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-architecture", choices=["dense", "moe"], required=True)
    parser.add_argument("--phase", choices=["decode", "prefill"], required=True)
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--shape", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--synthetic-zero-kv", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = run_case(args)
    print(canonical_json({"status": "written", "manifest": str(args.output / "manifest.json"), "qualification_eligible": manifest["qualification_eligible"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
