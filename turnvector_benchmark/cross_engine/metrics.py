from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError
from .contracts import bounded_string, finite_number, identifier, integer
from .openai import OpenAIStreamResult


TOKEN_AUTHORITIES = frozenset({"server_usage", "benchmark_tokenizer", "both"})


@dataclass(frozen=True)
class RequestObservation:
    request_id: str
    dispatch_ns: int
    terminal_ns: Optional[int]
    content_event_ns: Tuple[int, ...]
    wire_complete: bool
    benchmark_input_tokens: Optional[int]
    benchmark_output_tokens: Optional[int]
    server_input_tokens: Optional[int]
    server_output_tokens: Optional[int]
    token_authority: str
    required_output_tokens: Optional[int] = None
    output_obligations_met: bool = True
    error_class: Optional[str] = None
    slo_e2e_ms: Optional[float] = None
    slo_ttft_ms: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "dispatch_ns": self.dispatch_ns,
            "terminal_ns": self.terminal_ns,
            "content_event_ns": list(self.content_event_ns),
            "wire_complete": self.wire_complete,
            "benchmark_input_tokens": self.benchmark_input_tokens,
            "benchmark_output_tokens": self.benchmark_output_tokens,
            "server_input_tokens": self.server_input_tokens,
            "server_output_tokens": self.server_output_tokens,
            "token_authority": self.token_authority,
            "required_output_tokens": self.required_output_tokens,
            "output_obligations_met": self.output_obligations_met,
            "error_class": self.error_class,
            "slo_e2e_ms": self.slo_e2e_ms,
            "slo_ttft_ms": self.slo_ttft_ms,
        }


@dataclass(frozen=True)
class RequestMetrics:
    request_id: str
    canonical_input_tokens: Optional[int]
    canonical_output_tokens: Optional[int]
    ttft_ms: Optional[float]
    e2e_ms: Optional[float]
    stream_event_interval_ms: Tuple[float, ...]
    client_post_first_output_ms_per_token: Optional[float]
    effective_prefill_tokens_per_second: Optional[float]
    output_contract_ok: bool
    completion_valid: bool
    slo_satisfied: bool
    error_classes: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "canonical_input_tokens": self.canonical_input_tokens,
            "canonical_output_tokens": self.canonical_output_tokens,
            "ttft_ms": self.ttft_ms,
            "e2e_ms": self.e2e_ms,
            "stream_event_interval_ms": list(self.stream_event_interval_ms),
            "client_post_first_output_ms_per_token": self.client_post_first_output_ms_per_token,
            "effective_prefill_tokens_per_second": self.effective_prefill_tokens_per_second,
            "output_contract_ok": self.output_contract_ok,
            "completion_valid": self.completion_valid,
            "slo_satisfied": self.slo_satisfied,
            "error_classes": list(self.error_classes),
        }


@dataclass(frozen=True)
class TrialMetrics:
    interval_ms: float
    offered_request_count: int
    valid_completed_request_count: int
    failed_request_count: int
    canonical_output_tokens: int
    request_throughput: float
    output_throughput: float
    offered_request_rate: float
    completion_rate: float
    slo_goodput_ratio: float
    error_count: int
    error_counts: Mapping[str, int]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "interval_ms": self.interval_ms,
            "offered_request_count": self.offered_request_count,
            "valid_completed_request_count": self.valid_completed_request_count,
            "failed_request_count": self.failed_request_count,
            "canonical_output_tokens": self.canonical_output_tokens,
            "request_throughput": self.request_throughput,
            "output_throughput": self.output_throughput,
            "offered_request_rate": self.offered_request_rate,
            "completion_rate": self.completion_rate,
            "slo_goodput_ratio": self.slo_goodput_ratio,
            "error_count": self.error_count,
            "error_counts": dict(sorted(self.error_counts.items())),
        }


def observation_from_stream_result(
    request_id: str,
    result: OpenAIStreamResult,
    *,
    token_authority: str,
    benchmark_input_tokens: Optional[int] = None,
    benchmark_output_tokens: Optional[int] = None,
    required_output_tokens: Optional[int] = None,
    output_obligations_met: bool = True,
    slo_e2e_ms: Optional[float] = None,
    slo_ttft_ms: Optional[float] = None,
) -> RequestObservation:
    usage = result.completion.usage
    return RequestObservation(
        request_id=request_id,
        dispatch_ns=result.dispatch_ns,
        terminal_ns=result.completion.terminal_ns,
        content_event_ns=result.completion.content_event_ns,
        wire_complete=True,
        benchmark_input_tokens=benchmark_input_tokens,
        benchmark_output_tokens=benchmark_output_tokens,
        server_input_tokens=None if usage is None else usage.prompt_tokens,
        server_output_tokens=None if usage is None else usage.completion_tokens,
        token_authority=token_authority,
        required_output_tokens=required_output_tokens,
        output_obligations_met=output_obligations_met,
        error_class=None,
        slo_e2e_ms=slo_e2e_ms,
        slo_ttft_ms=slo_ttft_ms,
    )


def failed_observation(
    request_id: str,
    *,
    dispatch_ns: int,
    error_class: str,
    token_authority: str = "benchmark_tokenizer",
    benchmark_input_tokens: Optional[int] = None,
    required_output_tokens: Optional[int] = None,
    slo_e2e_ms: Optional[float] = None,
    slo_ttft_ms: Optional[float] = None,
) -> RequestObservation:
    return RequestObservation(
        request_id=request_id,
        dispatch_ns=dispatch_ns,
        terminal_ns=None,
        content_event_ns=(),
        wire_complete=False,
        benchmark_input_tokens=benchmark_input_tokens,
        benchmark_output_tokens=None,
        server_input_tokens=None,
        server_output_tokens=None,
        token_authority=token_authority,
        required_output_tokens=required_output_tokens,
        output_obligations_met=False,
        error_class=error_class,
        slo_e2e_ms=slo_e2e_ms,
        slo_ttft_ms=slo_ttft_ms,
    )


def _optional_tokens(value: Optional[int], where: str) -> Optional[int]:
    if value is None:
        return None
    return integer(value, where)


def _canonical_token_counts(observation: RequestObservation) -> Tuple[Optional[int], Optional[int]]:
    authority = observation.token_authority
    if authority not in TOKEN_AUTHORITIES:
        raise ContractError(f"token_authority must be one of {sorted(TOKEN_AUTHORITIES)!r}")
    benchmark_input = _optional_tokens(
        observation.benchmark_input_tokens, "benchmark_input_tokens"
    )
    benchmark_output = _optional_tokens(
        observation.benchmark_output_tokens, "benchmark_output_tokens"
    )
    server_input = _optional_tokens(observation.server_input_tokens, "server_input_tokens")
    server_output = _optional_tokens(observation.server_output_tokens, "server_output_tokens")
    if authority == "server_usage":
        if server_input is None or server_output is None:
            raise ContractError("server_usage token authority requires complete server usage")
        return server_input, server_output
    if authority == "benchmark_tokenizer":
        if benchmark_input is None:
            raise ContractError("benchmark_tokenizer authority requires an input token count")
        if observation.wire_complete and benchmark_output is None:
            raise ContractError("benchmark_tokenizer authority requires an output token count")
        return benchmark_input, benchmark_output
    if not observation.wire_complete:
        if benchmark_input is None:
            raise ContractError("both token authority requires a Benchmark input count for failures")
        return benchmark_input, None
    if None in (benchmark_input, benchmark_output, server_input, server_output):
        raise ContractError("both token authority requires Benchmark and server token counts")
    if benchmark_input != server_input or benchmark_output != server_output:
        raise ContractError("Benchmark and server token counts disagree")
    return benchmark_input, benchmark_output


def reduce_request_metrics(observation: RequestObservation) -> RequestMetrics:
    request_id = identifier(observation.request_id, "request_id")
    dispatch_ns = integer(observation.dispatch_ns, "dispatch_ns")
    if not isinstance(observation.wire_complete, bool):
        raise ContractError("wire_complete must be a boolean")
    if not isinstance(observation.output_obligations_met, bool):
        raise ContractError("output_obligations_met must be a boolean")
    terminal_ns = observation.terminal_ns
    if terminal_ns is not None:
        terminal_ns = integer(terminal_ns, "terminal_ns", minimum=dispatch_ns)
    if observation.wire_complete and terminal_ns is None:
        raise ContractError("wire_complete observation requires a terminal timestamp")
    if not observation.wire_complete and terminal_ns is not None:
        raise ContractError("incomplete observation cannot have a valid terminal timestamp")
    content_event_ns = tuple(
        integer(value, f"content_event_ns[{index}]", minimum=dispatch_ns)
        for index, value in enumerate(observation.content_event_ns)
    )
    if tuple(sorted(content_event_ns)) != content_event_ns:
        raise ContractError("content event timestamps must be monotone")
    if terminal_ns is not None and any(value > terminal_ns for value in content_event_ns):
        raise ContractError("content event timestamp appears after terminal")
    if not observation.wire_complete and content_event_ns:
        # Partial streams retain their raw events, but cannot enter favorable
        # request reducers. The timestamps remain structurally valid here.
        pass
    canonical_input, canonical_output = _canonical_token_counts(observation)
    required_output = observation.required_output_tokens
    if required_output is not None:
        required_output = integer(required_output, "required_output_tokens", minimum=1)
    if observation.error_class is not None:
        error_class = identifier(observation.error_class, "error_class")
    else:
        error_class = None
    if observation.slo_e2e_ms is not None:
        slo_e2e_ms = finite_number(observation.slo_e2e_ms, "slo_e2e_ms", minimum=0.0)
    else:
        slo_e2e_ms = None
    if observation.slo_ttft_ms is not None:
        slo_ttft_ms = finite_number(observation.slo_ttft_ms, "slo_ttft_ms", minimum=0.0)
    else:
        slo_ttft_ms = None

    ttft_ms = (
        None if not content_event_ns else (content_event_ns[0] - dispatch_ns) / 1_000_000.0
    )
    e2e_ms = None if terminal_ns is None else (terminal_ns - dispatch_ns) / 1_000_000.0
    intervals = tuple(
        (right - left) / 1_000_000.0
        for left, right in zip(content_event_ns, content_event_ns[1:])
    )
    post_first: Optional[float] = None
    if canonical_output is not None and canonical_output >= 2 and content_event_ns:
        post_first = (
            (content_event_ns[-1] - content_event_ns[0]) / 1_000_000.0
        ) / (canonical_output - 1)
    effective_prefill: Optional[float] = None
    if canonical_input is not None and ttft_ms is not None and ttft_ms > 0:
        effective_prefill = canonical_input / (ttft_ms / 1000.0)

    output_contract_ok = (
        required_output is None
        or (canonical_output is not None and canonical_output == required_output)
    )
    errors: List[str] = []
    if error_class is not None:
        errors.append(error_class)
    if not observation.wire_complete and error_class is None:
        errors.append("incomplete_stream")
    if not output_contract_ok:
        errors.append("output_contract")
    if not observation.output_obligations_met:
        errors.append("output_obligation")
    if required_output is not None and not content_event_ns:
        if "output_contract" not in errors:
            errors.append("output_contract")
        output_contract_ok = False
    completion_valid = (
        observation.wire_complete
        and output_contract_ok
        and observation.output_obligations_met
        and not errors
    )
    slo_satisfied = completion_valid and (
        slo_e2e_ms is None or (e2e_ms is not None and e2e_ms <= slo_e2e_ms)
    ) and (
        slo_ttft_ms is None or (ttft_ms is not None and ttft_ms <= slo_ttft_ms)
    )
    return RequestMetrics(
        request_id=request_id,
        canonical_input_tokens=canonical_input,
        canonical_output_tokens=canonical_output,
        ttft_ms=ttft_ms,
        e2e_ms=e2e_ms,
        stream_event_interval_ms=intervals,
        client_post_first_output_ms_per_token=post_first,
        effective_prefill_tokens_per_second=effective_prefill,
        output_contract_ok=output_contract_ok,
        completion_valid=completion_valid,
        slo_satisfied=slo_satisfied,
        error_classes=tuple(errors),
    )


def reduce_trial_metrics(
    requests: Sequence[RequestMetrics],
    *,
    t0_ns: int,
    t1_ns: int,
    planned_arrival_count: int,
    planned_arrival_interval_ns: int,
) -> TrialMetrics:
    start = integer(t0_ns, "t0_ns")
    end = integer(t1_ns, "t1_ns", minimum=start + 1)
    offered = integer(planned_arrival_count, "planned_arrival_count", minimum=1)
    arrival_interval = integer(
        planned_arrival_interval_ns, "planned_arrival_interval_ns", minimum=1
    )
    if len(requests) != offered:
        raise ContractError("request records must match every planned offered arrival")
    request_ids = [item.request_id for item in requests]
    if len(request_ids) != len(set(request_ids)):
        raise ContractError("request records contain duplicate request IDs")
    valid = [item for item in requests if item.completion_valid]
    output_tokens = sum(item.canonical_output_tokens or 0 for item in valid)
    interval_seconds = (end - start) / 1_000_000_000.0
    planned_seconds = arrival_interval / 1_000_000_000.0
    error_counts: Dict[str, int] = {}
    for item in requests:
        for error_class in item.error_classes:
            error_counts[error_class] = error_counts.get(error_class, 0) + 1
    failed = offered - len(valid)
    # Every invalid completion must have an explicit retained class.
    if failed != sum(not item.completion_valid for item in requests):
        raise ContractError("request completion accounting does not reconcile")
    if any(not item.completion_valid and not item.error_classes for item in requests):
        raise ContractError("invalid completion is missing an error class")
    good = sum(item.slo_satisfied for item in requests)
    return TrialMetrics(
        interval_ms=(end - start) / 1_000_000.0,
        offered_request_count=offered,
        valid_completed_request_count=len(valid),
        failed_request_count=failed,
        canonical_output_tokens=output_tokens,
        request_throughput=len(valid) / interval_seconds,
        output_throughput=output_tokens / interval_seconds,
        offered_request_rate=offered / planned_seconds,
        completion_rate=len(valid) / interval_seconds,
        slo_goodput_ratio=good / offered,
        error_count=failed,
        error_counts=error_counts,
    )


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    if isinstance(percentile, bool) or not isinstance(percentile, int):
        raise ContractError("percentile must be an integer")
    if percentile < 1 or percentile > 100:
        raise ContractError("percentile must be between 1 and 100")
    if not values:
        raise ContractError("nearest-rank reducer requires at least one value")
    parsed = [finite_number(value, f"values[{index}]") for index, value in enumerate(values)]
    ordered = sorted(parsed)
    rank = math.ceil((percentile / 100.0) * len(ordered))
    return ordered[rank - 1]


def summarize_observations(
    values: Iterable[float], *, unavailable_count: int = 0
) -> Mapping[str, Optional[float]]:
    parsed = [finite_number(value, f"values[{index}]") for index, value in enumerate(values)]
    unavailable = integer(unavailable_count, "unavailable_count")
    if not parsed:
        return {
            "count": 0,
            "unavailable_count": unavailable,
            "sum": 0.0,
            "min": None,
            "max": None,
            "mean": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    total = sum(parsed)
    return {
        "count": len(parsed),
        "unavailable_count": unavailable,
        "sum": total,
        "min": min(parsed),
        "max": max(parsed),
        "mean": total / len(parsed),
        "p50": nearest_rank(parsed, 50),
        "p95": nearest_rank(parsed, 95),
        "p99": nearest_rank(parsed, 99),
    }
