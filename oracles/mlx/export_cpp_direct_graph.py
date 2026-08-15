#!/usr/bin/env python3
"""Export the pinned full-model graph consumed by cpp_direct_oracle.cpp."""

from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Iterable, Tuple

import mlx.core as mx
from mlx_lm import load
from mlx_lm.models.cache import ConcatenateKVCache


SEED = 20260812


def validate_environment() -> None:
    required = {"mlx": "0.31.2", "mlx-lm": "0.31.3"}
    for package, expected in required.items():
        observed = importlib.metadata.version(package)
        if observed != expected:
            raise RuntimeError(f"requires {package}=={expected}; observed {observed}")


def deterministic_tokens(batch: int, length: int, vocab_size: int, seed: int) -> mx.array:
    values = [
        ((seed + row * 104729 + column * 8191) % (vocab_size - 1024)) + 1024
        for row in range(batch)
        for column in range(length)
    ]
    return mx.array(values, dtype=mx.int32).reshape(batch, length)


def cache_shape(model: Any, batch: int, length: int) -> Tuple[int, ...]:
    heads = int(model.args.num_key_value_heads)
    head_dim = int(getattr(model.args, "head_dim", 0))
    if head_dim == 0:
        head_dim = int(model.args.hidden_size // model.args.num_attention_heads)
    return batch, heads, length, head_dim


def make_inputs(
    model: Any, *, batch: int, token_length: int, cache_length: int, seed: int
) -> Tuple[mx.array, ...]:
    inputs = [deterministic_tokens(batch, token_length, model.args.vocab_size, seed)]
    shape = cache_shape(model, batch, cache_length)
    for _ in model.layers:
        inputs.extend((mx.zeros(shape, dtype=mx.bfloat16), mx.zeros(shape, dtype=mx.bfloat16)))
    return tuple(inputs)


def make_turn(model: Any):
    def turn(tokens: mx.array, *states: mx.array):
        caches = []
        for index in range(0, len(states), 2):
            cache = ConcatenateKVCache()
            cache.state = (states[index], states[index + 1])
            caches.append(cache)
        logits = model(tokens, cache=caches)[:, -1, :].astype(mx.float32)
        updated = tuple(value for cache in caches for value in cache.state)
        output_sum = mx.sum(logits)
        output_sumsq = mx.sum(logits * logits)
        return output_sum, output_sumsq, logits, *updated

    return turn


def signatures(model: Any) -> Iterable[Tuple[str, int, int, Tuple[mx.array, ...]]]:
    seen = set()
    for batch in (1, 4):
        for context in (512, 2048, 8192):
            for phase, token_length, cache_length in (
                ("decode", 1, context),
                ("decode-prime", context, 0),
            ):
                key = (batch, token_length, cache_length)
                if key not in seen:
                    seen.add(key)
                    yield phase, batch, context, make_inputs(
                        model,
                        batch=batch,
                        token_length=token_length,
                        cache_length=cache_length,
                        seed=SEED,
                    )
    for length in (64, 256, 1024):
        key = (1, length, 0)
        if key not in seen:
            seen.add(key)
            yield "prefill", 1, length, make_inputs(
                model, batch=1, token_length=length, cache_length=0, seed=SEED
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate_environment()
    model, _ = load(args.model, lazy=True)
    mx.eval(model.parameters())
    turn = make_turn(model)
    records = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with mx.exporter(str(args.output), turn, shapeless=False) as exporter:
        for phase, batch, shape, inputs in signatures(model):
            exporter(*inputs)
            records.append({"phase": phase, "batch": batch, "shape": shape})
            del inputs
            gc.collect()
            mx.clear_cache()
    manifest = args.output.with_suffix(args.output.suffix + ".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "turnvector.benchmark.cpp-direct-graph.v1",
                "seed": SEED,
                "model_path": str(args.model.resolve()),
                "graph_path": str(args.output.resolve()),
                "signatures": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
