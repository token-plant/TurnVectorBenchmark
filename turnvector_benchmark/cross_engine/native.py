"""Separate native-inference plan and raw-trial boundary (Slice F)."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from turnvector_benchmark.core import ContractError, IDENTIFIER_RE


NATIVE_PROFILE_SCHEMA = "turnvector.benchmark.cross-engine-native-inference.v1"
_REGISTERED_NATIVE_TOOLS = {
    "mlx-lm-benchmark": ("python3", "-m", "mlx_lm.benchmark"),
    "ax-engine-native": ("ax-engine-bench", "scenario"),
    "llama-cpp-bench": ("llama-bench",),
    "turnvector-native": ("turnvector-native-bench",),
}


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ContractError("%s must be an identifier" % where)
    return value


def _positive_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError("%s must be a positive integer" % where)
    return value


def _digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ContractError("%s must be a lowercase SHA-256" % where)
    return value


@dataclass(frozen=True)
class NativeInferencePlan:
    plan_id: str
    tool_id: str
    model_sha256: str
    tokenizer_sha256: str
    prompt_tokens: Tuple[int, ...]
    output_tokens: int
    warmups: int
    repetitions: int
    cooldown_seconds: float
    sampling_policy: str

    def __post_init__(self) -> None:
        _identifier(self.plan_id, "native plan ID")
        if self.tool_id not in _REGISTERED_NATIVE_TOOLS:
            raise ContractError("native tool is not registered")
        _digest(self.model_sha256, "model_sha256")
        _digest(self.tokenizer_sha256, "tokenizer_sha256")
        if not self.prompt_tokens:
            raise ContractError("prompt token shapes must not be empty")
        for value in self.prompt_tokens:
            _positive_integer(value, "prompt token shape")
        if len(self.prompt_tokens) != len(set(self.prompt_tokens)):
            raise ContractError("prompt token shapes must be unique")
        _positive_integer(self.output_tokens, "output tokens")
        if isinstance(self.warmups, bool) or not isinstance(self.warmups, int) or self.warmups < 0:
            raise ContractError("warmups must be a non-negative integer")
        _positive_integer(self.repetitions, "repetitions")
        if isinstance(self.cooldown_seconds, bool) or not isinstance(self.cooldown_seconds, (int, float)) or not math.isfinite(float(self.cooldown_seconds)) or self.cooldown_seconds < 0:
            raise ContractError("cooldown must be finite and non-negative")
        if self.sampling_policy not in {"greedy", "sampled"}:
            raise ContractError("native sampling policy is invalid")

    @property
    def command_prefix(self) -> Tuple[str, ...]:
        return _REGISTERED_NATIVE_TOOLS[self.tool_id]

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "schema_version": NATIVE_PROFILE_SCHEMA,
            "plan_id": self.plan_id,
            "tool_id": self.tool_id,
            "model_sha256": self.model_sha256,
            "tokenizer_sha256": self.tokenizer_sha256,
            "prompt_tokens": list(self.prompt_tokens),
            "output_tokens": self.output_tokens,
            "warmups": self.warmups,
            "repetitions": self.repetitions,
            "cooldown_seconds": float(self.cooldown_seconds),
            "sampling_policy": self.sampling_policy,
            "measurement_surface": "native_inference",
        }

    @property
    def sha256(self) -> str:
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class NativeTrial:
    case_id: str
    repetition: int
    prompt_tokens: int
    output_tokens: int
    prefill_seconds: float
    decode_seconds: float
    output_sha256: str

    @property
    def prefill_tokens_per_second(self) -> float:
        return self.prompt_tokens / self.prefill_seconds

    @property
    def decode_tokens_per_second(self) -> float:
        return self.output_tokens / self.decode_seconds


def parse_native_trials(path: Path, plan: NativeInferencePlan) -> Tuple[NativeTrial, ...]:
    trials = []
    try:
        lines = path.read_bytes().splitlines()
    except OSError as error:
        raise ContractError("cannot read native raw trials") from error
    for index, raw in enumerate(lines):
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContractError("native raw trial is invalid JSON") from error
        required = {"case_id", "repetition", "prompt_tokens", "output_tokens", "prefill_seconds", "decode_seconds", "output_sha256"}
        if not isinstance(value, dict) or set(value) != required:
            raise ContractError("native raw trial has an invalid field set")
        prompt = _positive_integer(value["prompt_tokens"], "trial prompt tokens")
        output = _positive_integer(value["output_tokens"], "trial output tokens")
        if prompt not in plan.prompt_tokens or output != plan.output_tokens:
            raise ContractError("native trial work differs from the frozen plan")
        repetition = _positive_integer(value["repetition"], "trial repetition")
        prefill = value["prefill_seconds"]
        decode = value["decode_seconds"]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)) or x <= 0 for x in (prefill, decode)):
            raise ContractError("native timing must be finite and positive")
        trials.append(NativeTrial(
            _identifier(value["case_id"], "trial case ID"), repetition, prompt, output,
            float(prefill), float(decode), _digest(value["output_sha256"], "output_sha256")
        ))
    expected = len(plan.prompt_tokens) * plan.repetitions
    if len(trials) != expected:
        raise ContractError("native raw trial count differs from the frozen plan")
    keys = [(trial.prompt_tokens, trial.repetition) for trial in trials]
    expected_keys = [(prompt, repetition) for prompt in plan.prompt_tokens for repetition in range(1, plan.repetitions + 1)]
    if keys != expected_keys:
        raise ContractError("native raw trials are missing, duplicated, or reordered")
    return tuple(trials)


def summarize_native_trials(trials: Iterable[NativeTrial]) -> Mapping[str, Any]:
    values = tuple(trials)
    if not values:
        raise ContractError("cannot summarize empty native trials")
    grouped: Dict[int, list[NativeTrial]] = {}
    for trial in values:
        grouped.setdefault(trial.prompt_tokens, []).append(trial)
    rows = []
    for prompt, group in sorted(grouped.items()):
        output_hashes = {trial.output_sha256 for trial in group}
        rows.append({
            "prompt_tokens": prompt,
            "trial_count": len(group),
            "prefill_tokens_per_second_median": statistics.median(trial.prefill_tokens_per_second for trial in group),
            "decode_tokens_per_second_median": statistics.median(trial.decode_tokens_per_second for trial in group),
            "output_deterministic": len(output_hashes) == 1,
        })
    return {"measurement_surface": "native_inference", "rows": rows}
