# AX Engine 7.1.5 vs mlx-lm 0.31.3 serving diagnostic

**Recorded:** 2026-08-29

**Status:** diagnostic, not formal paired publication evidence

**Raw observations:** [`results/cross-engine/ax-engine-7.1.5-vs-mlx-lm-0.31.3-qwen3-0.6b-20260829.json`](../results/cross-engine/ax-engine-7.1.5-vs-mlx-lm-0.31.3-qwen3-0.6b-20260829.json)

## Summary

On this small run, AX Engine had lower TTFT and higher output throughput for the short-prompt closed-loop, deterministic, and single-client cells. mlx-lm was substantially faster on the 1024-token long-context cell. At one offered request per second, open-loop completion throughput was effectively equal.

These results are useful for directing a larger experiment, but they do not establish a general winner. There were only one or two requests per cell, no warmup, and an eight-token output bound. In addition, AX Engine and mlx-lm reported different prompt-token counts for the same canonical prompt text; the long-context request was reported as 1,020 tokens by AX Engine and 1,024 by mlx-lm. The rows therefore do not satisfy the frozen same-resolved-model-work requirement for a formal paired claim.

## Environment

| Item | Value |
|---|---|
| Host | Apple M3 Ultra, 256 GiB unified memory |
| OS | macOS 26.4.1, arm64 |
| Model | `mlx-community/Qwen3-0.6B-4bit` |
| Model revision | `73e3e38d981303bc594367cd910ea6eb48349da8` |
| Model snapshot SHA-256 | `d8e7968c534bb2b2445ec765ec85cec9f22d107e5a5cbc7e1fa01de81411db9f` |
| Quantization | 4-bit groupwise, group size 64 |
| AX Engine | 7.1.5, HTTP/1.1 |
| mlx-lm / MLX | 0.31.3 / 0.32.2, HTTP/1.0 |
| mlx-lm chat-template args | `{"enable_thinking": false}` |

## Workload

- Both targets used the same canonical prompt generator and local model artifact.
- Streaming chat completions used `temperature=0`, `top_p=1`, `n=1`, and `seed=0`.
- Output work was bounded at eight server-reported completion tokens.
- There were no warmup requests.
- TTFT and E2E used client-side `time.monotonic_ns()` boundaries.
- Request and output throughput use the recorded trial interval, including scheduled open-loop arrival time; every interval is retained in the JSON for exact recomputation.
- Token counts came from each server's usage record and are diagnostic authority only.
- Closed-loop, deterministic, and open-loop cells used two measured requests; long-context and single-client cells used one.
- The open-loop cell offered one request per second. The long-context cell selected the 1,024-token prompt bucket.

Because output work is only eight tokens, “output throughput” below includes prefill and TTFT. It must not be interpreted as pure decode tokens per second.

## Results

Values in TTFT and E2E columns are arithmetic means of the raw requests in the linked JSON file.

| Scenario | AX TTFT (ms) | mlx-lm TTFT (ms) | AX E2E (ms) | mlx-lm E2E (ms) | AX output tok/s | mlx-lm output tok/s |
|---|---:|---:|---:|---:|---:|---:|
| Closed-loop, concurrency 1 | **91.80** | 167.89 | **158.93** | 189.75 | **99.73** | 67.19 |
| Deterministic, fresh session | **25.92** | 66.54 | **50.05** | 86.84 | **155.19** | 92.02 |
| Long context, 1,024-token bucket | 1,636.17 | **154.44** | 1,682.45 | **176.68** | 4.70 | **41.11** |
| Open loop, 1 offered req/s | **57.35** | 71.44 | **83.51** | 93.99 | **14.58** | 14.51 |
| Single-client, short prompt | **30.81** | 65.52 | **55.70** | 87.15 | **143.40** | 91.70 |

### Relative observations

| Scenario | TTFT observation | Output-throughput observation |
|---|---|---|
| Closed-loop | AX TTFT was 1.83× lower | AX was 1.48× higher |
| Deterministic | AX TTFT was 2.57× lower | AX was 1.69× higher |
| Long context | mlx-lm TTFT was 10.59× lower | mlx-lm was 8.76× higher |
| Open loop | AX TTFT was 1.25× lower | Difference was below 0.5% |
| Single-client | AX TTFT was 2.13× lower | AX was 1.56× higher |

“× lower” is the ratio of the larger latency to the smaller latency, not a percentage reduction. Ratios are descriptive calculations over these cells, not promotion or regression decisions.

## Raw TTFT observations

| Scenario | AX Engine (ms) | mlx-lm (ms) |
|---|---|---|
| Closed-loop | 65.918, 117.687 | 217.597, 118.176 |
| Deterministic | 27.050, 24.790 | 65.671, 67.411 |
| Long context | 1,636.171 | 154.436 |
| Open loop | 44.758, 69.935 | 68.559, 74.327 |
| Single-client | 30.814 | 65.522 |

## Interpretation and limits

1. **Short-prompt behavior favored AX Engine in this run.** Its mean TTFT was lower in four of the five cells, and its eight-token output throughput was higher in three short-prompt cells.
2. **The 1,024-token result favored mlx-lm strongly.** This warrants repetitions across 1,024, 2,048, 4,096, and 8,192-token buckets before attributing the result to a stable prefill difference.
3. **The open-loop result was throughput-neutral at this load.** Output throughput differed by less than 0.5%, while AX Engine had lower mean TTFT.
4. **Sample sizes are too small for percentile claims.** No p50, p95, p99, variance, confidence interval, or outlier decision is reported.
5. **Execution order was not balanced.** AX Engine was measured before mlx-lm. Thermal state, background activity, and cache effects may therefore confound the comparison.
6. **Token authority was not paired-publication quality.** AX Engine's usage included extensions outside the strict V1 dialect, and server token accounting differed between targets. Server usage was retained only as diagnostic authority.
7. **Formal host admission did not pass.** The separately frozen 48-cell mlx-lm publication profile rejected the host before execution because its load and swap limits were not satisfied. This diagnostic run must not be substituted for that publication.

These data authorize no favorable cross-engine claim, TurnVector qualification claim, native-inference claim, or production-readiness claim.
