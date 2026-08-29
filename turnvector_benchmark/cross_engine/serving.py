from __future__ import annotations

import hashlib
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError
from .campaign import CampaignCell
from .metrics import (
    RequestMetrics,
    RequestObservation,
    failed_observation,
    observation_from_stream_result,
    reduce_request_metrics,
    reduce_trial_metrics,
)
from .openai import OpenAIHTTPClient, OpenAIStreamResult, build_chat_request


Messages = Sequence[Mapping[str, str]]
PromptFactory = Callable[[CampaignCell, int], Messages]
TokenCounter = Callable[[Messages, str], Tuple[int, int]]


class HuggingFaceWorkload:
    """Local-only Benchmark tokenizer and deterministic serving prompt corpus."""

    def __init__(self, model_root: str) -> None:
        try:
            from transformers import AutoTokenizer  # type: ignore
        except ImportError as error:
            raise ContractError(
                "Benchmark token authority requires the optional transformers package"
            ) from error
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_root, local_files_only=True, trust_remote_code=False
            )
        except Exception as error:
            raise ContractError(f"cannot load local Benchmark tokenizer: {error}") from error
        self._long_prompts: Dict[int, Tuple[Mapping[str, str], ...]] = {}

    def count(self, messages: Messages, visible_output: str) -> Tuple[int, int]:
        try:
            prompt_ids = self.tokenizer.apply_chat_template(
                [dict(item) for item in messages],
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
                return_dict=False,
            )
            output_ids = self.tokenizer.encode(visible_output, add_special_tokens=False)
        except Exception as error:
            raise ContractError(f"Benchmark tokenizer failed: {error}") from error
        return len(prompt_ids), len(output_ids)

    def _long_prompt(self, bucket: int) -> Tuple[Mapping[str, str], ...]:
        if bucket in self._long_prompts:
            return self._long_prompts[bucket]
        if bucket < 1 or bucket > 131072:
            raise ContractError("prompt_token_bucket is outside the registered bound")
        prefix = "Benchmark input payload:"
        suffix = ". Continue the sequence without explanation: 1, 2, 3, 4, 5,"

        # For Qwen tokenizers, each appended ASCII marker contributes monotonically.
        # Search rather than assume that property and fail closed if no exact bucket exists.
        low, high = 0, bucket * 4
        exact: Optional[Tuple[Mapping[str, str], ...]] = None
        while low <= high:
            middle = (low + high) // 2
            candidate = (
                {"role": "user", "content": prefix + (" x" * middle) + suffix},
            )
            count, _ = self.count(candidate, "")
            if count == bucket:
                exact = candidate
                break
            if count < bucket:
                low = middle + 1
            else:
                high = middle - 1
        if exact is None:
            for middle in range(max(0, high - 8), min(bucket * 4, low + 8) + 1):
                candidate = (
                    {"role": "user", "content": prefix + (" x" * middle) + suffix},
                )
                count, _ = self.count(candidate, "")
                if count == bucket:
                    exact = candidate
                    break
        if exact is None:
            raise ContractError(
                f"Benchmark tokenizer cannot construct exact {bucket}-token prompt bucket"
            )
        self._long_prompts[bucket] = exact
        return exact

    def prompt(self, cell: CampaignCell, ordinal: int) -> Messages:
        parameters = cell.parameters
        long_bucket = parameters.get("prompt_token_bucket")
        if isinstance(long_bucket, int) and not isinstance(long_bucket, bool):
            return self._long_prompt(long_bucket)
        bucket = parameters.get("prompt_bucket", "short")
        session_state = parameters.get("session_state", "fresh")
        identity = 0 if session_state == "reused" else ordinal
        filler_count = (1000 if bucket == "medium" else 0) + identity
        if filler_count == 0:
            content = "Continue the sequence without explanation: 1, 2, 3, 4, 5,"
        else:
            content = (
                "Benchmark input payload:"
                + (" x" * filler_count)
                + ". Continue the sequence without explanation: 1, 2, 3, 4, 5,"
            )
        return ({"role": "user", "content": content},)


@dataclass(frozen=True)
class _CompletedRequest:
    ordinal: int
    phase: str
    planned_dispatch_ns: int
    result: Optional[OpenAIStreamResult]
    observation: RequestObservation
    metrics: Optional[RequestMetrics]
    finish_reason_ok: bool


class CommonServingExecutor:
    """Benchmark-owned executor for the five conventional serving families.

    It consumes only the frozen scenario and campaign cell. Engine summaries are
    neither accepted nor consulted; timing starts immediately before the client
    dispatch and stream event timestamps are recorded by ``OpenAIHTTPClient``.
    """

    def __init__(
        self,
        *,
        scenarios: Mapping[str, Mapping[str, Any]],
        prompt_factory: PromptFactory,
        token_counter: TokenCounter,
        response_dialects: Optional[Mapping[str, str]] = None,
        clock: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not scenarios:
            raise ContractError("serving executor requires frozen scenarios")
        self.scenarios = dict(scenarios)
        self.prompt_factory = prompt_factory
        self.token_counter = token_counter
        self.response_dialects = dict(response_dialects or {})
        self.clock = clock
        self.sleep = sleep

    @staticmethod
    def _positive_int(value: Any, where: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ContractError(f"{where} must be a positive integer")
        return value

    @staticmethod
    def _number(value: Any, where: str, *, minimum: float = 0.0) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ContractError(f"{where} must be a number")
        parsed = float(value)
        if parsed < minimum or parsed == float("inf") or parsed != parsed:
            raise ContractError(f"{where} is outside its finite bound")
        return parsed

    def _request(
        self,
        *,
        cell: CampaignCell,
        endpoint: Mapping[str, Any],
        scenario: Mapping[str, Any],
        ordinal: int,
        phase: str,
        planned_dispatch_ns: int,
    ) -> _CompletedRequest:
        messages = tuple(dict(item) for item in self.prompt_factory(cell, ordinal))
        if not messages:
            raise ContractError("prompt factory returned no messages")
        benchmark_input, _ = self.token_counter(messages, "")
        output_tokens = self._positive_int(
            scenario["output_work"]["required_output_tokens"],
            "scenario.output_work.required_output_tokens",
        )
        request = build_chat_request(
            model=endpoint["model_ids"][0],
            messages=messages,
            output_tokens=output_tokens,
            output_bound_field=scenario["request_dialect"]["output_bound_field"],
            include_usage=True,
            seed=cell.repetition,
        )
        timeout = self._number(
            scenario["protocol"]["timeout_seconds"],
            "scenario.protocol.timeout_seconds",
            minimum=0.001,
        )
        dialect = self.response_dialects.get(cell.target_id, "strict_v1")
        dispatch_fallback = self.clock()
        try:
            result = OpenAIHTTPClient(
                endpoint,
                timeout_seconds=timeout,
                clock=self.clock,
                response_dialect=dialect,
            ).complete(request)
            _, benchmark_output = self.token_counter(messages, result.completion.visible_text)
            usage = result.completion.usage
            finish_ok = (
                result.completion.finish_reason == "length"
                or (
                    result.completion.finish_reason in {"stop", "eos_token"}
                    and usage is not None
                    and usage.completion_tokens == output_tokens
                )
            )
            observation = observation_from_stream_result(
                f"{cell.cell_id}.{phase}.q{ordinal:04d}",
                result,
                token_authority=scenario["metric_eligibility"]["token_authority"],
                benchmark_input_tokens=benchmark_input,
                benchmark_output_tokens=benchmark_output,
                required_output_tokens=output_tokens,
                output_obligations_met=finish_ok,
                slo_e2e_ms=self._number(scenario["slos"]["e2e_ms"], "scenario.slos.e2e_ms"),
                slo_ttft_ms=self._number(scenario["slos"]["ttft_ms"], "scenario.slos.ttft_ms"),
            )
            metrics = None if phase == "warmup" else reduce_request_metrics(observation)
            return _CompletedRequest(
                ordinal, phase, planned_dispatch_ns, result, observation, metrics, finish_ok
            )
        except Exception as error:
            error_text = str(error).lower()
            error_name = error.__class__.__name__.lower()
            if "token counts disagree" in error_text:
                error_class = "token_count_disagreement"
            elif "timeout" in error_name or "timed out" in error_text:
                error_class = "timeout"
            else:
                error_class = "request_failed"
            observation = failed_observation(
                f"{cell.cell_id}.{phase}.q{ordinal:04d}",
                dispatch_ns=dispatch_fallback,
                error_class=error_class,
                token_authority=scenario["metric_eligibility"]["token_authority"],
                benchmark_input_tokens=benchmark_input,
                required_output_tokens=output_tokens,
                slo_e2e_ms=self._number(scenario["slos"]["e2e_ms"], "scenario.slos.e2e_ms"),
                slo_ttft_ms=self._number(scenario["slos"]["ttft_ms"], "scenario.slos.ttft_ms"),
            )
            metrics = None if phase == "warmup" else reduce_request_metrics(observation)
            return _CompletedRequest(
                ordinal, phase, planned_dispatch_ns, None, observation, metrics, False
            )

    def _sequential(
        self, cell: CampaignCell, endpoint: Mapping[str, Any], scenario: Mapping[str, Any], count: int, t0: int
    ) -> List[_CompletedRequest]:
        return [
            self._request(
                cell=cell,
                endpoint=endpoint,
                scenario=scenario,
                ordinal=ordinal,
                phase="measured",
                planned_dispatch_ns=t0,
            )
            for ordinal in range(count)
        ]

    def _closed_loop(
        self, cell: CampaignCell, endpoint: Mapping[str, Any], scenario: Mapping[str, Any], count: int, t0: int
    ) -> List[_CompletedRequest]:
        concurrency = self._positive_int(
            cell.parameters.get("concurrency", scenario["arrival_trace"].get("concurrency")),
            "cell.parameters.concurrency",
        )
        with ThreadPoolExecutor(max_workers=min(concurrency, count)) as pool:
            futures = [
                pool.submit(
                    self._request,
                    cell=cell,
                    endpoint=endpoint,
                    scenario=scenario,
                    ordinal=ordinal,
                    phase="measured",
                    planned_dispatch_ns=t0,
                )
                for ordinal in range(count)
            ]
            return sorted((future.result() for future in as_completed(futures)), key=lambda item: item.ordinal)

    def _open_loop(
        self, cell: CampaignCell, endpoint: Mapping[str, Any], scenario: Mapping[str, Any], count: int, t0: int
    ) -> Tuple[List[_CompletedRequest], int]:
        rate = self._number(
            cell.parameters.get("offered_load_rps"),
            "cell.parameters.offered_load_rps",
            minimum=0.000001,
        )
        interval_ns = max(1, int((count / rate) * 1_000_000_000))
        futures: List[Future[_CompletedRequest]] = []
        with ThreadPoolExecutor(max_workers=count) as pool:
            for ordinal in range(count):
                planned = t0 + int((ordinal / rate) * 1_000_000_000)
                remaining = planned - self.clock()
                if remaining > 0:
                    self.sleep(remaining / 1_000_000_000.0)
                futures.append(
                    pool.submit(
                        self._request,
                        cell=cell,
                        endpoint=endpoint,
                        scenario=scenario,
                        ordinal=ordinal,
                        phase="measured",
                        planned_dispatch_ns=planned,
                    )
                )
            completed = sorted((future.result() for future in futures), key=lambda item: item.ordinal)
        return completed, interval_ns

    def run(
        self, *, cell: CampaignCell, endpoint: Mapping[str, Any], collector: Any
    ) -> Mapping[str, Any]:
        del collector  # host/process evidence is collected independently by the controller.
        try:
            scenario = self.scenarios[cell.scenario_id]
        except KeyError as error:
            raise ContractError(f"no frozen scenario for {cell.scenario_id!r}") from error
        protocol = scenario["protocol"]
        raw_warmups = protocol["warmup_repetitions"]
        if isinstance(raw_warmups, bool) or not isinstance(raw_warmups, int) or raw_warmups < 0:
            raise ContractError("warmup_repetitions must be a non-negative integer")
        warmups = raw_warmups
        for ordinal in range(warmups):
            result = self._request(
                cell=cell,
                endpoint=endpoint,
                scenario=scenario,
                ordinal=ordinal,
                phase="warmup",
                planned_dispatch_ns=self.clock(),
            )
            if not result.observation.wire_complete:
                raise ContractError("serving warmup failed; measured execution is not eligible")

        count = self._positive_int(
            scenario["arrival_trace"]["requests_per_trial"], "requests_per_trial"
        )
        mode = scenario["arrival_trace"]["mode"]
        t0 = self.clock()
        planned_interval_ns = 1
        if mode in {"single", "sequential"}:
            completed = self._sequential(cell, endpoint, scenario, count, t0)
        elif mode == "closed_loop":
            completed = self._closed_loop(cell, endpoint, scenario, count, t0)
        elif mode == "open_loop":
            completed, planned_interval_ns = self._open_loop(
                cell, endpoint, scenario, count, t0
            )
        else:
            raise ContractError(f"unsupported serving arrival mode {mode!r}")
        t1 = max(
            self.clock(),
            t0 + 1,
            *(item.observation.terminal_ns or item.observation.dispatch_ns for item in completed),
        )
        if mode != "open_loop":
            planned_interval_ns = max(1, t1 - t0)
        request_metrics = [item.metrics for item in completed]
        if any(item is None for item in request_metrics):
            raise ContractError("measured request is missing reduced metrics")
        reduced = [item for item in request_metrics if item is not None]
        trial = reduce_trial_metrics(
            reduced,
            t0_ns=t0,
            t1_ns=t1,
            planned_arrival_count=count,
            planned_arrival_interval_ns=planned_interval_ns,
        )

        request_trace: List[Mapping[str, Any]] = []
        stream_events: List[Mapping[str, Any]] = []
        output_hashes: List[Mapping[str, Any]] = []
        for item in completed:
            observation = item.observation
            first = observation.content_event_ns[0] if observation.content_event_ns else None
            request_trace.append(
                {
                    "request_id": observation.request_id,
                    "cell_id": cell.cell_id,
                    "pairing_id": cell.pairing_id,
                    "parameters": dict(cell.parameters),
                    "planned_dispatch_ns": item.planned_dispatch_ns,
                    "dispatch_ns": observation.dispatch_ns,
                    "dispatch_lateness_ns": max(0, observation.dispatch_ns - item.planned_dispatch_ns),
                    "first_content_ns": first,
                    "terminal_ns": observation.terminal_ns,
                    "wire_complete": observation.wire_complete,
                    "error_class": observation.error_class,
                }
            )
            if item.result is not None:
                completion = item.result.completion
                for event in completion.raw_events:
                    row = dict(event.as_dict())
                    row["request_id"] = observation.request_id
                    row["event_kind"] = "data"
                    row["data_sha256"] = hashlib.sha256(event.data.encode("utf-8")).hexdigest()
                    stream_events.append(row)
                for comment in completion.raw_comments:
                    row = dict(comment.as_dict())
                    row["request_id"] = observation.request_id
                    row["event_kind"] = "comment"
                    row["line_sha256"] = hashlib.sha256(comment.line.encode("utf-8")).hexdigest()
                    stream_events.append(row)
                output_hashes.append(
                    {
                        "request_id": observation.request_id,
                        "visible_sha256": completion.visible_sha256,
                        "finish_reason": completion.finish_reason,
                        "finish_reason_ok": item.finish_reason_ok,
                        "server_output_tokens": None if completion.usage is None else completion.usage.completion_tokens,
                        "benchmark_output_tokens": observation.benchmark_output_tokens,
                    }
                )
        cooldown = self._number(protocol["cooldown_seconds"], "cooldown_seconds")
        if cooldown:
            self.sleep(cooldown)
        return {
            "schema_version": "turnvector.benchmark.common-serving-observations.v1",
            "wire_dialect": self.response_dialects.get(cell.target_id, "strict_v1"),
            "arrival_mode": mode,
            "matrix_id": cell.matrix_id,
            "parameters": dict(cell.parameters),
            "request_trace": request_trace,
            "stream_events": stream_events,
            "raw_trials": [
                {
                    "cell_id": cell.cell_id,
                    "pairing_id": cell.pairing_id,
                    "scenario_id": cell.scenario_id,
                    "repetition": cell.repetition,
                    "t0_ns": t0,
                    "t1_ns": t1,
                    "trial_metrics": trial.as_dict(),
                }
            ],
            "request_metrics": [item.as_dict() for item in reduced],
            "trial_metrics": trial.as_dict(),
            "output_hashes": output_hashes,
        }


__all__ = [
    "CommonServingExecutor",
    "HuggingFaceWorkload",
    "PromptFactory",
    "TokenCounter",
]
