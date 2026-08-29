# Cross-Engine and OpenAI Serving Benchmark Design V1

## 1. Status and purpose

This document defines a design contract, not a measured result and not an implementation authorization. It extends TurnVectorBenchmark from a TurnVector qualification repository into an independent cross-engine measurement authority while preserving the existing qualification boundary.

The first executable cross-engine profile is named **`cross-engine-openai-serving-v1`**. It measures TurnVector, AX Engine, `mlx-lm`, `llama.cpp`, and future engines through an explicitly declared OpenAI-compatible serving surface. That surface is never represented as the locked TurnVector production Data Plane and can never satisfy a `direct_data_plane` qualification requirement.

The design has six goals:

1. benchmark-owned throughput, TTFT, TPOT, routing, prefix reuse, MTP, determinism, and regression evidence;
2. direct execution of AX Engine, `mlx-lm`, and `llama.cpp`, rather than uncritical ingestion of their self-reported summaries;
3. reusable scenario contracts for future engine comparisons;
4. a mandatory conventional-metrics core with capability-conditioned advanced measurements;
5. reuse of applicable measurement ideas from existing serving, cross-model, memory, observability, and recovery lanes; and
6. strict separation among qualification, serving comparison, native inference comparison, and engine-owned diagnostic artifacts.

## 2. Non-negotiable boundaries

### 2.1 Qualification remains unchanged

`turnvector-implementation-v2`, its 12 lanes, 425 cases, 58 gates, `SubjectAdapter v1`, and the locked TurnVector Data Plane remain the complete TurnVector qualification contract. A cross-engine run:

- cannot produce `full_implementation_status: passed`;
- cannot satisfy or weaken a qualification lane;
- cannot relabel an OpenAI endpoint as `turnvector.data-plane`;
- cannot use a transport proxy to manufacture production Data Plane evidence; and
- cannot turn an unsupported engine capability into a pass or a zero-valued metric.

### 2.2 Benchmark custody

TurnVectorBenchmark owns scenario expansion, request generation, dispatch timestamps, streaming parsing, host sampling, raw observations, reducers, gates, comparison pairing, reports, and checksums. An engine adapter owns only lifecycle and endpoint discovery. It may start, stop, and configure the real engine, but it does not send timed requests, compute benchmark metrics, select thresholds, or declare pass/fail.

### 2.3 Evidence dimensions never merge silently

Evidence identity is a product of orthogonal dimensions, never one overloaded enum:

- `measurement_surface`: `openai_serving` or, in a later profile, `native_inference`;
- `comparison_form`: `absolute`, `paired_delta`, or `regression`;
- `semantic_claim`: `serving`, `route`, `prefix_reuse`, `mtp`, `other_speculative`, or `determinism`;
- `observation_level`: `client_only`, `declared`, `corroborated`, or `benchmark_forced`; and
- `provenance_class`: `benchmark_measurement` or `diagnostic_import`.

A row carries exactly one value on every applicable axis. `diagnostic_import` is never a Benchmark measurement and cannot satisfy a gate. Rows from different surfaces, session modes, tokenization identities, timing boundaries, or incompatible claim kinds are not combined into one throughput table. The future schemas enforce these axes and forbid qualification fields such as `full_implementation_status`, qualification lane outcomes, and any TurnVector Data Plane descriptor.

## 3. Architecture

```text
CrossEngineProfile + Scenario Manifests + Target Manifests
  -> CrossEngineController
  -> EngineLifecycleAdapter
  -> real OpenAI-compatible endpoint
  -> Benchmark-owned OpenAI Client + Host/Process Collectors
  -> Raw Request / Stream / Route / Host Evidence
  -> Benchmark Reducers
  -> Evidence Gates + Optional Promotion Gates
  -> Per-target Rows + Pairwise Comparisons + Reports
```

### 3.1 Fixed registries, not plugin discovery

The controller uses fixed registries for:

- profile versions;
- scenario families;
- lifecycle adapter protocol versions;
- OpenAI request/stream decoders;
- normalized route mappers; and
- metric reducers.

A target manifest selects registered behavior; importing an arbitrary Python plugin from a manifest is forbidden.

### 3.2 EngineLifecycleAdapter V1

The lifecycle adapter is a strict JSONL control process:

```text
hello -> prepare_session -> start_target -> describe_endpoint
      -> reset_state* -> stop_target -> shutdown
```

The command is an argv array with no shell. Standard error is bounded. Every request has one response. The adapter returns process identities and an endpoint descriptor but never forwards benchmark traffic.

The endpoint descriptor is the closed `openai-serving-endpoint-v1` shape:

```json
{
  "protocol_family": "openai-compatible",
  "protocol_version": "turnvector.benchmark.openai-serving.v1",
  "transport": "http",
  "base_url": "http://127.0.0.1:31418/v1",
  "api_flavor": "chat_completions",
  "stream_format": "sse-data-json",
  "model_ids": ["qwen3.6-27b"],
  "process_ids": [1234],
  "capability_report_sha256": "<64 lowercase hex>",
  "authentication_env_var": null
}
```

Only parsed literal loopback addresses in `127.0.0.0/8` or `::1` are admitted in V1; hostnames, userinfo, fragments, ambiguous encodings, redirects, proxy environment variables, remote listeners, TLS bypasses, and adapter-mediated timed requests are rejected. The controller verifies that the listener belongs to a returned PID and that every PID descends from the adapter-started process group. Authentication is referenced only by `authentication_env_var`; the value is redacted from argv, environment evidence, HTTP traces, diagnostics, stderr, reports, and checksums.

The endpoint schema has `additionalProperties: false` and constants for protocol family/version, transport, API flavor, and stream format. It explicitly forbids `data_plane`, `turnvector.data-plane`, `unix_stream`, `socket_path`, `descriptor_sha256`, and every qualification execution-boundary field.

### 3.2.1 Lifecycle state machine

Every lifecycle line is compact UTF-8 JSON with a final LF, at most 64 KiB, and exactly `{kind,protocol_version,request_id,payload}`. Responses echo `request_id` and are exactly `{kind,protocol_version,request_id,status,payload,error}`; `status` is `ok` or `error`, exactly one of `payload`/`error` is non-null, and `error` is a registered `{code,message}` without secrets. Duplicate/unknown IDs, unknown fields, malformed UTF-8/JSON, oversized lines, EOF, timeout, nonzero exit, or a response in the wrong state are contract failures.

The legal states and transitions are:

```text
SPAWNED --hello/hello_ack--> NEGOTIATED
NEGOTIATED --prepare_session/prepared--> PREPARED
PREPARED --start_target/target_started--> STARTED
STARTED --describe_endpoint/endpoint_ready--> READY
READY --reset_state/state_reset--> READY                 (zero or more)
READY --stop_target/target_stopped--> STOPPED
STARTED --stop_target/target_stopped--> STOPPED          (startup failure cleanup)
STOPPED --shutdown/shutdown_ack--> TERMINATED
```

The closed payload projections are:

| Request / response | Required payload |
| --- | --- |
| `hello` / `hello_ack` | requested adapter protocol; adapter ID/version, registered target family, supported lifecycle capabilities |
| `prepare_session` / `prepared` | run/session IDs, Benchmark state root, target/config/model digests, reset policy; echoed identities and initial state-root inventory digest |
| `start_target` / `target_started` | frozen argv/config/environment-name allowlist and readiness deadline; process-group leader, child PID/executable records, start timestamp |
| `describe_endpoint` / `endpoint_ready` | expected target/session IDs; closed endpoint descriptor and listener-owner identity |
| `reset_state` / `state_reset` | reset ordinal/policy and expected prior inventory; pre/post PID records and inventory digests |
| `stop_target` / `target_stopped` | stop reason/deadline and expected process group; exit records, surviving PID/listener arrays (both required empty for success) |
| `shutdown` / `shutdown_ack` | session ID; final bounded diagnostic count and no-live-child assertion |

Every identity is echoed and compared; every array is ordered and bounded; schemas define exact scalar domains and forbid extra fields.

Each request has a profile-frozen timeout; timeout never implies success. `prepare_session` receives only a Benchmark-created state root and frozen target/config identities. `start_target` returns the process-group leader, child PIDs, executable identities, and readiness deadline. `describe_endpoint` succeeds only after an independent Benchmark connection/readiness probe. `reset_state` names one frozen reset policy and returns pre/post process identities and state-root inventory digests; an acknowledgement alone is not proof. `stop_target` must terminate only the adapter-owned process group and prove no returned PID/listener survives. On interruption the controller stops issuing measured requests, writes an interruption bundle, requests `stop_target`, then `shutdown`; it never kills an unrelated process. Cleanup failure is retained independently and cannot replace the primary measurement failure.

Adapter commands are selected from the fixed registry; a target manifest binds arguments to that registered command but cannot name arbitrary executable code. Mutable target/cache state is confined to Benchmark-created roots. The controller records bounded stdout/stderr, disables inherited proxy variables, inventories allowed roots, and atomically creates an absent/empty output root.

### 3.3 First-party target adapters

The initial target registry contains:

| Target | Real execution surface | Initial support |
| --- | --- | --- |
| `turnvector` | TurnVector OpenAI-compatible server | common serving; route/prefix/MTP when exposed |
| `ax-engine` | AX Engine server OpenAI API | common serving, multi-model, route, prefix, MTP when configured |
| `mlx-lm` | pinned `mlx_lm.server` | common serving; advanced capabilities explicit |
| `llama.cpp` | pinned `llama-server` | common serving, server-exposed cache/route facts where available |

The adapter records exact executable hashes, argv, environment allowlist, package/build revisions, model snapshots, tokenizer identity, topology, and all child PIDs. `python -m mlx_lm.server` and `llama-server` are admitted real target surfaces, not proxies pretending to be TurnVector.

### 3.4 Future native-inference profile

Raw model-inference throughput and kernel/prefill comparisons have a different timing boundary from HTTP serving. They will use a separate `cross-engine-native-inference-v1` profile and cannot reuse OpenAI-serving rows as direct throughput. Engine-owned tools such as `mlx_lm.benchmark` may be run as pinned native targets in that later profile, but their raw trials must still be captured and reduced under Benchmark custody.

### 3.5 Benchmark OpenAI Serving Dialect V1

The V1 common path is exactly `POST {base_url}/chat/completions`, HTTP/1.1, UTF-8 `application/json`, with redirects, automatic retries, compression, tools, images/audio, multiple choices, logprobs, and target-specific request extensions forbidden. The canonical request is a closed object containing `model`, ordered `messages` (`system`/`user` text only), `stream=true`, `stream_options={"include_usage":true}` when supported by the frozen capability, `temperature=0`, `top_p=1`, `n=1`, `seed` when supported, and exactly one profile-selected output-bound field (`max_tokens` or `max_completion_tokens`). A target unable to map these semantics exactly is `profile_incompatible` for common comparison, not silently rewritten by its adapter.

Success is HTTP 200 with `text/event-stream`. Non-200 responses use a bounded closed error projection and never contribute favorable metrics. The parser incrementally decodes arbitrary TCP/UTF-8 fragmentation into SSE lines; admits only `data:` events plus blank separators; rejects unknown event fields, invalid UTF-8/JSON, multiple choices, index drift, content after terminal, duplicate terminal, missing terminal, and bytes beyond frozen response limits. A stream consists of zero or more role/reasoning/content deltas, exactly one terminal chunk with a non-null `finish_reason`, optional exactly one usage-bearing terminal chunk in the frozen admitted position, then exactly one `[DONE]`, then EOF/connection reuse with no additional stream data. The Benchmark retains every raw bounded event and its receipt timestamp.

Reasoning content is a separately declared channel and never silently added to user-visible output. Canonical visible output is the byte-for-byte concatenation of admitted UTF-8 content deltas in order. Mandatory OpenAI-visible determinism compares these bytes, finish reason, terminal sequence, and usage identity. Native token IDs/timestamps are a separate capability and must be request-correlated raw evidence.

## 4. Contracts and identities

### 4.1 Profile

`profiles/cross-engine-openai-serving-v1.json` will bind:

- profile and schema versions;
- scenario IDs and exact manifest digests;
- required conventional metrics;
- capability-conditioned metric groups;
- process, cache, order, warmup, repetition, and cooldown policies;
- host admission limits;
- percentile and aggregate reducers;
- evidence and promotion gates; and
- comparison and publication policy.

This profile is independent of `performance-publication-v1`. The existing profile remains TurnVector publication authority; common reducers may later be factored only after byte-compatible tests establish equivalence.

Each scenario manifest is a closed object binding `schema_version`, `id`, `family`, `activation`, `required_capabilities`, ordered matrix dimensions, corpus/prompt and tokenizer identities, request dialect/options, fixed versus natural output-work contract, arrival trace, SLOs, warmup/repetition/cooldown, process/cache/connection isolation, metric eligibility, reducers, gates, required raw artifacts, and claim boundaries. Expansion is lexical scenario order → declared matrix order → dimension-value order → repetition order, producing stable case/pairing IDs before startup. Target manifests cannot add/drop dimensions, requests, metrics, reducers, or gates.

### 4.2 Target manifest

A target manifest binds:

- `engine_family`: `turnvector`, `ax-engine`, `mlx-lm`, `llama.cpp`, or a subsequently registered value;
- engine source revision and dirty state;
- adapter ID/version, registered command identity, and bounded declarative arguments;
- executable and dependency identities;
- endpoint/API flavor;
- process topology;
- model, quantization, tokenizer, chat-template, MTP sidecar, and runtime-config identities;
- supported scenario/capability declarations; and
- route mapper version, if route normalization is supported.

The engine family is descriptive identity, never a special-case scoring rule.

### 4.3 Model-equivalence class

Every comparison row declares both a workload-equivalence contract and a model-equivalence class.

Workload contracts are:

- `same_api_workload` — identical serialized user-visible semantics and fixed output obligations; valid for endpoint-serving comparisons only, never equal native engine work; or
- `same_resolved_model_work` — additionally identical upstream model/revision, architecture/configuration, tokenizer and special-token map, resolved chat template, resolved input token IDs/hash, context policy, output-work contract, sampling semantics, and comparable quantization.

Model classes are:

- `exact_artifact` — same weight bytes, tokenizer, template, quantization, sampling, and verified resolved input;
- `same_source_model` — same upstream source but engine-specific conversion/quantization covered by a pre-run digest-bound approval recording conversion tools, algorithm, bit width, group size, tensor coverage, serialized weight size, and a frozen model-quality parity suite;
- `shape_matched` — same architecture/parameter class and request shape only; or
- `capability_demo` — no cross-engine performance ratio is permitted.

Headline paired intrinsic-engine ratios require `same_resolved_model_work` plus `exact_artifact`, or an approved `same_source_model` contract whose parity suite passes. A missing/failed conversion approval downgrades the row to `shape_matched`. `same_api_workload` and `shape_matched` rows may report absolute endpoint values side by side but cannot be called equal-work engine speedups.

### 4.4 Session identity

A session binds the Benchmark revision, profile, scenarios, targets, execution order, host, OS, power, thermal/load admission, models, prompts/tokens, sampling, endpoint topology, cache state, warmups, repetitions, cooldown, and start/end conditions. Before target startup, an immutable campaign manifest freezes the complete cell plan, corpus provenance/strata/weights, target and repetition order, retry/outlier/timeout policies, pairing keys, reducers, uncertainty method, practical-effect threshold, and primary report. The default outlier policy is no deletion.

Every launched attempt receives a monotone ordinal and remains in `attempts.jsonl`. Runtime errors, timeouts, crashes, and slow trials are not discarded or retried for performance reasons. Only a predeclared contract/environment-invalid reason may authorize a retry; all attempts remain linked and the frozen first-eligible-attempt rule selects the primary result. A corpus is called representative only when its domain, provenance, inclusion/exclusion, deduplication, strata, and weights were frozen without reference to observed engine results.

A `paired_delta` additionally requires:

- same admitted host session;
- identical scenario and request sequence;
- compatible model-equivalence class;
- balanced or alternating target order;
- isolated target process/cache state as declared;
- complete denominator digest and pairing-key digest; and
- no unrecorded process or configuration change.

## 5. Capability model and partial coverage

### 5.1 Conventional core

A target participates in the profile only if it supports the selected OpenAI request family and the common metric core:

- successful and failed request counts;
- authoritative input/output token counts or a benchmark-owned pinned tokenizer count;
- E2E latency;
- TTFT for streaming requests;
- client-observed content-event intervals and post-first-content amortized time per canonical output token;
- native token ITL/TPOT/decode rate only when validated token IDs and Benchmark-clock-correlated emission timestamps are exposed;
- request throughput;
- output-token throughput;
- offered load and completion rate;
- output identity/completeness; and
- SLO goodput under a frozen SLO contract.

A missing required wire observation after a target declared and passed preflight is `contract_failed`, not `unsupported`. A mathematically ineligible metric, such as post-first-output amortization for a valid one-token natural completion, is recorded as `unavailable` under the frozen eligibility rule and is never replaced by zero. Failure to satisfy fixed output work is an evidence/goodput failure, not malformed evidence. Pre-run `profile_incompatible` applies whenever the exact selected OpenAI dialect or any mandatory common-core prerequisite cannot be exposed; it is broader than complete absence of an OpenAI API.

### 5.2 Capability-conditioned groups

Advanced groups are negotiated independently:

- `route_reporting`;
- `prefix_cache_memory`;
- `prefix_cache_disk`;
- `mtp`;
- `multi_model`;
- `cancellation`;
- `server_queue_metrics`;
- `process_memory_attribution`;
- `restartable_state`; and
- `native_output_identity`.

Each scenario declares required capabilities. The result for a missing optional capability is `unsupported` with a stable reason code. `not_applicable` is reserved for a scenario that has no semantic meaning for the selected target configuration. No unsupported cell enters an aggregate, geometric mean, rank, or denominator.

### 5.3 Comparison intersection

A pairwise comparison includes only cells supported by both targets and reports:

- complete requested cell count;
- common supported cell count;
- target-only cells;
- peer-only cells; and
- excluded cells with reasons.

The report must never present an intersection-only result as coverage of the complete profile. Every table and favorable claim displays requested/common/target-only/peer-only cell counts and `coverage_status`; zero common cells produce no comparison row.

### 5.4 Capability authority and state transition

Capability disposition follows one exact chain: target-manifest declaration → Benchmark preflight probe → frozen case plan → runtime verification. The Benchmark owns applicability; the adapter or target cannot self-select `not_applicable`. A mismatch between declaration and preflight/runtime is `contract_failed`. Stable dispositions are `supported`, `capability_unsupported`, `profile_incompatible`, `environment_unavailable`, and `not_applicable`, each with a registered reason code. `profile_incompatible` is decided before measurement when the exact V1 OpenAI dialect cannot be exposed; an optional scenario capability uses `capability_unsupported`. Runtime fallback never retroactively removes a planned cell.

## 6. Metric definitions

All times use a Benchmark-owned monotonic clock. OpenAI SSE frames are content events, not token events: no reducer assumes one content delta equals one model token. Every scenario freezes HTTP client/version, request serialization, connection-open/keepalive/pool policy, connection limit, compression, timeout, redirect, and retry policy. `t_dispatch` is captured immediately before invoking the serialized request under that policy; connection setup is either included for every target or performed in an identically labeled preconnection phase.

| Metric | Definition |
| --- | --- |
| `ttft_ms` | `t_first_nonempty_content - t_dispatch`; headers, role-only, reasoning-only when excluded by the scenario, and empty deltas do not stop the clock |
| `e2e_ms` | `t_valid_terminal - t_dispatch` for a complete stream/response |
| `stream_event_interval_ms` | intervals between consecutive non-empty content events; raw series retained and never labeled token ITL |
| `client_post_first_output_ms_per_token` | `(t_last_nonempty_content - t_first_nonempty_content) / (canonical_output_tokens - 1)` for at least two canonical output tokens; framing-sensitive descriptive diagnostic only, never a favorable cross-target, regression, MTP-speedup, SLO, or promotion metric |
| `token_itl_ms` / `native_tpot_ms` | available only with validated native token IDs and Benchmark-clock-correlated emission timestamps |
| `native_decode_tokens_per_second` | available only under the same native timing contract; never derived by assigning one timestamp to every token in an SSE chunk |
| `effective_prefill_tokens_per_second` | canonical input tokens divided by TTFT, explicitly client-effective and never kernel prefill throughput |
| `request_throughput` | valid completed requests divided by the complete measured interval |
| `output_throughput` | canonical tokens from valid completed requests divided by the complete measured interval |
| `offered_request_rate` | all planned open-loop arrivals divided by the planned arrival interval; late, failed, cancelled, and unfinished requests remain in denominators |
| `slo_goodput_ratio` | requests satisfying completion, fixed output work, applicable latency, and output obligations divided by all offered requests |
| `error_count` | transport, protocol, timeout, server, incomplete-stream, and output-contract errors, retained by class |

For a measured trial, `t0` is the frozen trial origin before the first planned arrival and `t1` is the terminal event or frozen global deadline of the final planned request. Throughput uses `t1 - t0`; drain time is never discarded. Fixed-work cells declare `required_output_tokens`. Early EOS, truncation, empty output, missing terminal state, or another output count is an output-contract failure and remains in offered/error denominators. Natural-completion workloads are a distinct scenario type and cannot be mixed with fixed-work speed comparisons. Fully buffered/coalesced streams may retain TTFT, E2E, request throughput, and output throughput but cannot support favorable token-ITL or native-decode claims.

Nearest-rank p50/p95/p99, min, max, sum, count, and arithmetic mean are recomputed from raw observations where declared. Per-request eligibility is evaluated before aggregation; unavailable values are counted and excluded only under the frozen eligibility rule. A summary mismatch is a contract failure.

Token counting has three explicit authorities:

1. `server_usage`, which validates target accounting but is insufficient by itself for a cross-target token-rate ratio;
2. `benchmark_tokenizer`, using an exact pinned tokenizer snapshot and canonical UTF-8 output; or
3. `both`, which requires equality and is mandatory for equal-work token-rate comparisons.

A same-work prefill claim additionally requires exact resolved input-token identity or a request-correlated server digest tied to the pinned tokenizer/template. If chat templates or resolved prompt tokens differ, counts are shown separately and no equal-work prefill or intrinsic engine-speed ratio is permitted.

## 7. Scenario families

The scenario model borrows useful workload shapes from AX Engine but uses TurnVectorBenchmark-owned manifests, IDs, corpora, reducers, and gates. AX manifests are references, not imported authority.

### 7.1 V1 required common scenarios

1. **`single-client-streaming`**
   - short and medium prompts;
   - fixed output target;
   - streaming completion;
   - TTFT, content-event intervals, client post-first-output amortization, E2E, output throughput, and effective-prefill rate; native TPOT/ITL/decode only when observable.

2. **`closed-loop-concurrency`**
   - concurrency 1/2/4/8 where host admission permits;
   - fixed request count and corpus order;
   - request/output throughput and latency tails.

3. **`open-loop-load-sweep`**
   - deterministic arrival trace;
   - monotone offered-load levels plus bounded refinement near saturation;
   - late requests continue to terminal state;
   - SLO goodput, queueing visible to the client, completion, and errors.

4. **`long-context-serving`**
   - 1K/2K/4K/8K admitted prompt-token buckets;
   - fixed output work;
   - TTFT, effective prefill, E2E, memory high-water, output identity.

5. **`deterministic-repeat`**
   - fresh and reused sessions;
   - identical prompt tokens, seed, sampler, output bound, and target state;
   - exact canonical visible-output byte hash comparison; derived/native token hashes only at their declared observation levels.

### 7.2 Capability-conditioned scenarios

6. **`shared-prefix-fanout`** — common prefix plus distinct suffixes, concurrency and reuse depth.
7. **`prefix-restart-restore`** — cold process, warm memory, and restart/disk states separated.
8. **`multi-model-coexistence`** — alternating and overlapping model traffic with per-model fairness and progress.
9. **`prefill-vs-decode-interference`** — long Prefill competing with interactive Decode.
10. **`cancellation-and-disconnect`** — cancellation acknowledgement, terminal behavior, leaked work, and subsequent availability.
11. **`mtp-acceleration`** — direct denominator versus MTP route on the same target/model/prompt suite.
12. **`route-fallback`** — requested route, selected route, fallback reason, and output parity.
13. **`prefix-churn-pressure`** — bounded cache churn, eviction, recovery, and no-corruption checks.

V2 candidates may add embeddings, multimodal, speculative methods other than MTP, continuous batching ceilings, and distributed targets. They do not enter V1 by ad hoc manifest fields.

### 7.3 Multi-model metrics and claim boundary

A multi-model cell freezes the resident model set, deployment topology, per-model arrival traces and output work, configured service weights, concurrency, memory budget, and admission policy. It reports per-model offered load, completion rate, output throughput, SLO goodput, maximum progress gap, useful-service share, target weighted-service share, and weighted-service error; zero completions or an unavailable configured model is a failure, not an omitted cell. Aggregate throughput is always accompanied by per-model rows and cannot hide starvation.

One-process and process-per-model systems may be compared only as explicitly named deployment topologies. Such a result is not intrinsic scheduler superiority unless model artifacts, resident set, memory budget, arrivals, output work, and a separately frozen scheduler-cost boundary are equivalent.

## 8. Routing evidence

### 8.1 Native and normalized routes

The Benchmark retains the target's complete native route record and separately derives a normalized projection through a versioned mapper:

```text
backend: mlx | llama.cpp | other
execution: direct | mtp | ngram | speculative | delegated
cache: none | memory_prefix | disk_prefix
model_state: cold | resident | restored
fallback: none | unsupported | correctness | capacity | runtime_error
```

Unknown native values remain preserved and map to `other`; they are never guessed. A normalized mapper cannot turn an absent native fact into a positive route claim.

### 8.2 Route evidence levels

- `client_only`: endpoint and model response are known; no internal route claim.
- `declared`: target returned a route field, retained but not independently corroborated.
- `corroborated`: route is tied to process/config identity and route-specific counters or traces.
- `benchmark_forced`: the Benchmark selected mutually exclusive target configurations and verified process/config identity.

Prefix, MTP, or fallback publication gates require `corroborated` or `benchmark_forced`. Common serving metrics require only `client_only` and remain valid without a route claim. An unknown/`other` execution, cache, backend, or fallback value cannot satisfy a positive route-purity gate.

### 8.3 Request attribution and real-engine proof

Every request carries a Benchmark-generated correlation ID in a frozen admitted request field/header. Registered per-engine evidence-source contracts state whether native facts arrive through response fields/headers, bounded logs/traces, metrics snapshots, or Benchmark-forced mutually exclusive configurations. They freeze capture barriers, before/after counter semantics, timestamp mapping, artifact descriptors, and attribution reducers. Aggregate counters or `reset_state` acknowledgements alone cannot prove a per-request route.

Before and after every measured cell, the Benchmark hashes the target executable/config/model identities, verifies the listener-owning process tree, captures route/config state, and checks it again after shutdown. A client-only row from an endpoint whose actual backend is unproved is labeled `endpoint_serving`; it cannot support an intrinsic named-engine, route, prefix, MTP, or fallback claim.

## 9. Prefix-reuse contract

Prefix reuse is measured as separate state rows, never inferred from a faster second request alone:

- `cold_process_cold_cache`;
- `warm_process_empty_prefix_cache`;
- `warm_memory_prefix_hit`;
- `restarted_disk_prefix_hit`; and
- `post_churn_recovery`.

The manifest freezes exact eligible prefix token IDs, block-rounding semantics, minimum reuse coverage, suffix token IDs, reuse group, request order, concurrency, process/cache isolation, and expected output work. Required raw evidence includes request timestamps, output hashes, memory/process samples, native route records, and hit/miss token or byte counters. Configured-reuse aggregates are intention-to-treat: misses and partial hits remain in the candidate aggregate. Disk reuse additionally proves a new process identity and absence of surviving process-local state.

A positive prefix claim requires:

1. output parity with the cold denominator;
2. a corroborated/forced prefix route;
3. verified route-specific hit accounting, after frozen block-rounding, meets the frozen minimum reuse coverage for eligible prefix tokens; nonzero but below-threshold reuse is retained as partial-reuse evidence and cannot satisfy a positive gate;
4. no unrecorded model/process/config change; and
5. latency/throughput deltas reported separately from route correctness.

Targets without route facts may still produce ordinary repeated-prompt serving rows, labeled `client_only`; those rows cannot claim prefix reuse.

## 10. MTP contract

MTP is capability-conditioned. A valid MTP comparison binds:

- exact model and MTP sidecar/package identities;
- direct and MTP configurations of the same target;
- identical prompt-token suite, sampling, seed, output-token target, warmups, repetitions, cooldown, and host session;
- direct route for the denominator and MTP route for the candidate;
- exact output-token identity for headline speedup; a registered, Benchmark-owned semantic evaluator is capability-demo evidence only unless its corpus, thresholds, length obligations, and evaluator ID were frozen before execution;
- drafted, accepted, rejected, and emitted token counts;
- acceptance ratio and fallback count; and
- TTFT, output throughput, effective prefill, E2E, and errors; framing-sensitive client amortization remains diagnostic, and native TPOT/decode is used only when observable.

`mtp_accept_ratio = accepted_draft_tokens / drafted_tokens`; zero drafted tokens makes it unavailable, not zero. Every planned configured-MTP request remains in an intention-to-treat aggregate. A fallback is negative route evidence and contributes to fallback/error/completion accounting; it is never deleted from the candidate. `pure_mtp_speedup` is permitted only when fallback count is zero, greedy or reproducibly coupled sampling is used, fixed output work is identical, direct/MTP output-token identity holds, and drafted/accepted/rejected/emitted/terminal counts reconcile with `0 <= accepted <= drafted`. Otherwise the report shows configured-MTP performance and fallback rate without a pure MTP speedup. N-gram and other speculative routes use distinct `semantic_claim` values and cannot be labeled MTP.

Cross-engine MTP ratios require compatible MTP semantics and model-equivalence class. Otherwise each engine is compared only with its own same-session direct denominator.

## 11. Determinism contract

Determinism concerns output and declared execution identity, not equal wall-clock timing. Mandatory OpenAI-visible output determinism compares:

- exact canonical visible UTF-8 output bytes and hash;
- finish reason, authoritative usage, and terminal/error sequence; and
- request/config/model identities.

A Benchmark-retokenized hash is labeled `derived_output_token_hash`; `native_output_token_hash` is emitted only when validated native token IDs are exposed. Route sequence, cache transition, and engine-native trace digest are compared only at their declared observation levels. The report independently records `output_deterministic`, `route_deterministic`, `state_deterministic`, and `not_observable`. `not_observable` cannot satisfy a positive native-token, route, cache, or state determinism gate, and timing similarity is never determinism proof.

## 12. Regression and baseline contract

A trusted baseline is created only by a separate append-only promotion operation. Its receipt binds promotion authority, timestamp, complete evidence-root digest, profile/scenario/target/model identities, physical-host fingerprint, and superseded baseline digest. Promotion is atomic create-only; overwrite and selecting the current run as its own denominator are forbidden.

The candidate campaign manifest binds the baseline digest and regression gates before any target starts. A baseline promoted after candidate execution begins is inapplicable. Same-host regression requires the same physical-host fingerprint, hardware configuration, OS/power mode, dependencies, model/tokenizer/template, target configuration, timing boundary, token authority, and workload. A host-class-only comparison is labeled cross-host directional evidence and cannot pass a regression promotion gate without a separately frozen calibration contract.

The profile freezes regression gates before execution, for example:

- maximum TTFT/TPOT/E2E increase;
- minimum throughput/goodput ratio;
- zero new output, completion, route, or determinism violations;
- bounded memory high-water increase; and
- no loss of supported capability cells.

A performance regression may remain publishable negative evidence when contract, correctness, route, and host-admission gates pass. Evidence validity and performance promotion remain separate decisions.

## 13. Reuse of existing lanes and measurement ideas

The cross-engine profile reuses semantics, not qualification claims:

| Existing area | Reused concept | Cross-engine adaptation |
| --- | --- | --- |
| `request-serving-lifecycle` | streaming completion, cancellation, disconnect, backpressure, at-most-once output | OpenAI HTTP/SSE client owned by Benchmark |
| `cross-model-serving` | per-model throughput, weighted progress, switch/interference evidence | capability-conditioned multi-model scenarios |
| `residency-and-memory-governor` | process footprint, pressure, reclaim convergence | host/process sampling plus target-specific route/state evidence |
| `observability-qualification` | telemetry off/on, attribution, overhead | normalized route mapper and Benchmark-owned host tracing |
| `persistence-and-recovery` | restart, corruption/churn, state identity | prefix restart/restore and churn scenarios where target exposes bounded state roots |
| `performance-publication-v1` | session identity, raw trials, reducers, host admission, publication/promotion split | common cross-engine contract patterns, with separate profile/schema identities |
| AX scenario/replay benchmarks | shared prefix, retained prefix, mixed models, cancellation, long Prefill, serving corpus, route checks | Benchmark-owned manifests and neutral engine vocabulary |

No existing lane is marked supported merely because a concept appears in this table.

## 14. Artifacts and reports

Every run first writes one canonical `artifact_manifest.json`. It enumerates the exact required/optional artifact IDs, relative POSIX paths, media/schema types, custody, byte sizes, SHA-256 digests, and ordered cardinalities. The profile freezes per-file and total byte limits. IDs and paths are unique; undeclared files are forbidden except registered bounded diagnostics. Before use, the controller rejects absolute/traversal/alias paths, symlinks, special files, hard-link identity conflicts, size/digest mismatch, and writes outside the Benchmark-created root. `SHA256SUMS` is derived from the closed manifest and is not an independent authority.

Success, capability disposition, contract failure, infrastructure failure, and interruption each have closed root schemas. Failure/interruption bundles still require campaign/run/target/environment identities, attempt log, ordered diagnostics, process/cleanup audit, artifact manifest, and checksums; metric files are present only when their status permits them. Reports are derived views and cannot introduce a fact absent from governed raw evidence.

Every supported target/session writes:

- `run_manifest.json`;
- `target_manifest.json`;
- `environment.json`;
- `capabilities.json`;
- `scenario_plan.json`;
- `attempts.jsonl`;
- `request_trace.jsonl`;
- `stream_events.jsonl`;
- `raw_trials.jsonl`;
- `host_samples.jsonl`;
- `process_audit.jsonl`;
- `output_hashes.jsonl`;
- optional `native_routes.jsonl`;
- optional `normalized_routes.jsonl`;
- `metrics.json`;
- `gates.json`;
- `report.json`;
- `report.md`; and
- `SHA256SUMS`.

Pairwise comparison output additionally includes `pairing.json`, `coverage_intersection.json`, `comparison_rows.jsonl`, and separate absolute, paired, capability, and diagnostic tables.

Engine-owned benchmark artifacts may be attached under `diagnostic_imports/` with source revision, command, schema, and digest. Their summaries are never copied into Benchmark-owned metric rows. To obtain a Benchmark-owned result, the controller must execute the target and retain its own raw observations.

## 15. Status and failure model

Reports expose orthogonal axes rather than one overloaded status:

- `contract_status`: `valid`, `invalid`, or `interrupted`;
- `capability_status`: `supported`, `capability_unsupported`, `profile_incompatible`, `not_applicable`, or `environment_unavailable`;
- `execution_status`: `not_started`, `completed`, `partial`, or `infrastructure_failed`;
- `evidence_status`: `not_evaluated`, `publishable`, or `not_publishable`;
- `promotion_status`: `not_evaluated`, `not_applicable`, `passed`, or `failed`; and
- `coverage_status`: `complete`, `partial`, or `zero_common_cells`.

Faults are evaluated in pipeline order: contract/preflight → capability/environment → lifecycle/execution → wire/output evidence → metric/evidence gates → promotion gates → coverage/comparison. The first failing stage is primary, later independent failures are retained in ordered diagnostics, and cleanup failure never replaces the primary fault. An invalid/interrupted contract makes evidence and promotion `not_evaluated`; unsupported/not-applicable cells have no measured metrics; promotion failure may coexist with publishable negative evidence. Parent status is a deterministic projection of every planned child, never a vote over completed cells.

Independent target/scenario cells continue after another cell fails when process and host safety permit. All failures retain bounded evidence. There is no single winner score.

## 16. CLI design

Planned commands:

```bash
python3 -B -m turnvector_benchmark inspect-cross-engine \
  --profile profiles/cross-engine-openai-serving-v1.json

python3 -B -m turnvector_benchmark run-cross-engine \
  --profile profiles/cross-engine-openai-serving-v1.json \
  --target targets/ax-engine-openai-v1.json \
  --target targets/mlx-lm-openai-v1.json \
  --target targets/llama-cpp-openai-v1.json \
  --output /outside/git/cross-engine-evidence

python3 -B -m turnvector_benchmark compare-cross-engine \
  --evidence /outside/git/cross-engine-evidence \
  --require-common-core
```

The profile binds scenario-set paths and digests; the run command cannot override them. `inspect` is side-effect free. `run` requires an absent/empty outside-repository output root, writes the pre-run campaign manifest before startup, and retains interruption/partial bundles. `compare` validates all evidence independently and rejects zero common cells under `--require-common-core`. Baseline promotion is a separate create-only command, never a side effect of run/compare.

Exit codes are `0` for valid requested evidence, `2` for contract/preflight invalidity, `3` for non-publishable evidence, `4` for explicit profile/capability incompatibility, `5` for failed required promotion, and `6` for interruption/infrastructure failure. For mixed cells, deterministic precedence is `2`, `6`, `3`, `4`, `5`, then `0`; reports retain every cell axis regardless of process exit. Secrets are provided outside manifests and redacted from all evidence channels.

## 17. Fairness and publication rules

1. Freeze workload, target order, model-equivalence class, tokenizer, sampling, SLOs, warmups, repetitions, cooldown, cache/process isolation, and reducers before execution.
2. Use identical prompt token IDs where every target admits token input; otherwise freeze prompt text and independently retain each pinned tokenizer result.
3. Preserve full output work. Truncated, empty, early-EOS, or failed completions do not improve throughput.
4. Continue late open-loop requests to terminal state or a frozen global deadline; record unfinished work.
5. Alternate target-first order and isolate caches/processes according to the scenario.
6. Admit only stable external power/power mode, Low Power Mode, thermal, load, memory pressure/swap, process priority, display/background-workload policy, and competing-process conditions; bind exact OS, MLX, Metal, HTTP-client, and runtime versions.
7. Separate client-effective prefill from native kernel prefill.
8. Separate endpoint throughput from direct engine throughput.
9. Report absolute distributions before ratios; report coverage beside every comparison.
10. Do not rank unsupported cells, impute missing values, or publish one composite winner score.
11. A smoke corpus validates the harness only; publication requires a frozen representative corpus and prompt-mix table.
12. Every favorable claim links to raw trials, target identity, model identity, host identity, exact Benchmark revision, and complete campaign/attempt history.
13. Pairing keys and ratio direction are formed before aggregation; the profile fixes mean-of-paired-ratios versus ratio-of-aggregates and they are never interchanged.
14. A generalized favorable claim requires a pre-run uncertainty method and practical-effect threshold whose favorable confidence bound crosses that threshold; otherwise report observed samples only.
15. Isolation policies are exact: `fresh_process_fresh_state_root`, `fresh_process_preserved_disk_state`, `session_reuse_memory_warm`, or `no_reset`. Each requires PID and pre/post state-root inventory proof; inability to establish it is explicit unsupported/environment status.
16. The run manifest and every target repository/build identity are captured before and after execution; drift invalidates evidence.

## 18. Schemas and implementation surface

A future implementation is expected to add, without changing qualification protocols:

- `profiles/cross-engine-openai-serving-v1.json`;
- `scenarios/openai-serving-common-v1.json` and capability scenario sets;
- `targets/*.json` examples for the four initial engines;
- `schemas/cross-engine-profile-v1.schema.json`;
- `schemas/cross-engine-target-v1.schema.json`;
- `schemas/cross-engine-scenario-set-v1.schema.json`;
- `schemas/cross-engine-lifecycle-v1.schema.json`;
- `schemas/openai-serving-endpoint-v1.schema.json`;
- `schemas/openai-serving-request-v1.schema.json` and strict stream/error event schemas;
- `schemas/cross-engine-artifact-manifest-v1.schema.json`;
- `schemas/cross-engine-evidence-v1.schema.json`;
- `schemas/cross-engine-report-v1.schema.json`;
- `schemas/cross-engine-baseline-receipt-v1.schema.json`;
- `turnvector_benchmark/cross_engine/` for contracts, controller, OpenAI client, lifecycle, collectors, reducers, route mapping, comparison, and reports;
- `adapters/cross_engine/` first-party lifecycle adapters; and
- focused fixtures/tests that never load production models in ordinary CI.

The existing `turnvector_benchmark/data_plane.py`, `protocols/data_plane_v1.proto`, qualification expectation, qualification suites, and `SubjectAdapter v1` are not modified to admit OpenAI serving.

## 19. Implementation slices and acceptance sequence

### Slice A — contracts and fixture target

- strict profile/target/scenario/evidence schemas;
- deterministic plan expansion;
- lifecycle adapter protocol;
- Benchmark-owned OpenAI/SSE parser;
- synthetic OpenAI fixture server;
- common metric reducers and exact golden artifacts.

Acceptance: malformed HTTP/SSE, missing terminal events, token-count disagreement, output truncation, unsupported capabilities, and artifact path attacks fail closed.

### Slice B — common serving targets

- AX Engine, `mlx_lm.server`, `llama-server`, and TurnVector lifecycle adapters;
- single-client, concurrency, open-loop, long-context, and deterministic-repeat scenarios;
- host/process collectors and balanced paired sessions.

Acceptance: every target produces the conventional core or an explicit pre-run incompatibility; common-cell coverage is exact; no target-specific summary enters reducers.

### Slice C — route and prefix reuse

- native route capture and versioned normalization;
- shared-prefix, restart restore, and churn scenarios;
- route evidence levels and positive prefix gates.

Acceptance: a latency-only warmup cannot pass a prefix gate; absent route evidence remains client-only.

### Slice D — MTP and multi-model

- same-target direct denominators;
- drafted/accepted/emitted accounting;
- route fallback handling;
- multi-model coexistence and Prefill/Decode interference.

Acceptance: direct fallback cannot be reported as MTP; unsupported peers remain explicit; output correctness precedes speedup.

### Slice E — regression and publication

- immutable baseline promotion;
- applicability checks;
- frozen regression gates;
- comparison reports and publication validation.

Acceptance: mismatched host/model/profile baselines are not applicable; negative performance remains publishable when evidence gates pass.

### Slice F — native inference, separately authorized

Design and implement `cross-engine-native-inference-v1` for direct engine throughput and pinned `mlx_lm.benchmark`-style baselines. It must not change the timing meaning of OpenAI-serving metrics.

## 20. Test matrix

At minimum, tests cover:

- strict schemas, unknown/missing/mistyped fields, identifier and digest grammar;
- deterministic plan ordering and exact case counts;
- adapter lifecycle, process identity, PID reuse, timeout, crash, and stderr caps;
- loopback endpoint restrictions, redirects, malformed HTTP, malformed SSE, duplicate terminal events, missing usage, and partial streams;
- exact TTFT/TPOT/ITL/E2E boundaries using a controlled clock;
- zero/one/many output-token cases and early EOS;
- open-loop arrival drift and late completion;
- token-authority agreement/disagreement;
- balanced target order and cache/process isolation;
- route mapper known/unknown values and evidence levels;
- prefix hit/miss/restart/churn positive and counterexample cases;
- MTP direct/fallback/zero-draft/partial-accept/full-accept cases;
- output, route, and state determinism independently;
- unsupported/not-applicable/environment/contract/infrastructure distinctions;
- baseline applicability and exact/one-past regression gates;
- artifact containment, symlink, size, digest, and checksum failures;
- comparison intersection and no-imputation behavior;
- static proof that OpenAI adapters do not import or emit the TurnVector Data Plane descriptor;
- reference fixture end-to-end runs in CI without model downloads;
- exact lifecycle state/response/timeout/crash/orphan/cleanup/interruption fixtures with expected orthogonal statuses and CLI exits;
- SSE UTF-8/TCP fragmentation, multi-token content chunks, role/reasoning-only chunks, `[DONE]`/usage ordering, duplicate/missing terminals, retries, connection reuse, and fully buffered streams;
- selective retry, dropped first attempt, post-run corpus substitution, reordered trials, ratio-of-means versus mean-of-paired-ratios, and insufficient-repetition favorable claims;
- listener/PID mismatch, hidden delegation, proxy environment leakage, auth-secret leakage, unsafe reset roots, baseline races/chronology, and same-run baseline selection; and
- simultaneous-fault fixtures that assert primary precedence, ordered secondary diagnostics, required failure/interruption artifacts, metric eligibility, and exit code.

Real-model nightly jobs are separate, host-labeled, and never prerequisites for ordinary contract tests.

## 21. Risks and claim limits

- OpenAI compatibility does not imply identical tokenization, scheduling, route semantics, or completion behavior.
- Client TTFT includes transport/server framing and cannot be called native prefill latency.
- Server usage may be missing or inaccurate; token authority must remain explicit.
- Route normalization can lose detail, so native records are always retained.
- A repeated-prompt speedup does not prove prefix reuse without corroborating route evidence.
- MTP methods may differ enough that only same-target direct ratios are defensible.
- Multi-model topology differs across one-process and process-per-model engines; topology is part of the row identity, not normalized away.
- Engine-owned benchmark artifacts are useful diagnostic context but do not become independent evidence by import.
- Cross-engine comparison cannot establish TurnVector qualification, product readiness, or universal engine superiority.
- The frozen AX/TurnVector scheduler theory remains analytic until a separately reviewed measurement matrix binds its required cost, arrival, switch, SLO, and output identities.

## 22. Design decisions frozen by V1

1. The first cross-engine profile is explicitly OpenAI serving.
2. OpenAI serving never impersonates the TurnVector production Data Plane.
3. Conventional client-observed metrics are required for participating targets.
4. Route, prefix, MTP, multi-model, memory, and restart capabilities are independently optional.
5. AX Engine, `mlx-lm`, and `llama.cpp` are real targets executed by Benchmark-owned clients.
6. Engine-owned summaries are diagnostic imports only.
7. Scenario ideas may be borrowed; manifests, corpora, reducers, gates, and evidence remain Benchmark-owned.
8. Comparisons operate on an explicit supported-cell intersection and disclose coverage.
9. MTP speedup requires a same-target direct denominator and route proof.
10. Prefix-reuse claims require output parity and corroborated/forced hit evidence.
11. Determinism is decomposed into output, route, and state identity.
12. Regression baselines are immutable and applicability-checked.
13. Serving and native-inference timing boundaries remain separate profiles.
14. There is no composite winner score.

## 23. Independent review disposition

Three independent read-only reviews were run with distinct lenses: architecture/protocol compatibility, measurement methodology/fairness, and adversarial implementation completeness. All three returned `REVISION_REQUIRED` on the initial draft. Their valid blockers are incorporated into this revision:

- exact runtime separation from the TurnVector Data Plane and forbidden qualification fields;
- closed lifecycle state machine, process/listener proof, cleanup, trust, secret, and state-root boundaries;
- frozen OpenAI request/SSE dialect and correction of the false SSE-event-equals-token assumption;
- canonical-text versus derived/native-token determinism;
- workload/model equivalence and resolved-input proof;
- capability authority, coverage disclosure, and orthogonal statuses with precedence;
- request-correlated route/prefix/MTP evidence and intention-to-treat fallback/miss accounting;
- multi-model fairness/topology claim boundaries;
- pre-run campaign, attempt retention, corpus/statistical claim controls;
- immutable chronological baseline promotion bound to physical host identity;
- closed artifact/failure/interruption custody and CLI exit semantics; and
- fixture tests with exact expected transitions, statuses, artifacts, eligibility, and exits.

The reviews made no code or file changes. After the corrections above, the architecture/protocol reviewer returned `PASS`, the methodology/fairness reviewer returned `PASS`, and the adversarial completeness reviewer returned `PASS_WITH_NONBLOCKERS` with no remaining blocker. This disposition records design corrections only; implementation still requires separate authorization and slice-specific review.

## 24. Explicit mlx-lm 0.31 absolute publication profile

The strict cross-engine V1 wire dialect remains unchanged. `mlx-lm` 0.31 is measured by the separately named `mlx-lm-openai-serving-publication-v1` profile, whose claim scope is `absolute_single_target` and whose response dialect is explicitly `mlx_lm_0_31`. It is not a proxy and does not rewrite traffic.

The dialect admits only the observed, bounded differences from strict V1: HTTP/1.0 responses, `: keepalive N/M` comments retained as raw evidence, `delta.reasoning`, a repeated assistant role in terminal deltas, `chat.completion` on the post-terminal usage chunk, and a closed set of token-detail usage fields. Every raw data event and comment retains its Benchmark receipt timestamp. Unknown comments, fields, orderings, terminal content, and usage details fail closed.

The profile freezes all five conventional serving families for 48 measured cells, including three repetitions, required warmup and cooldown, exact matrix parameters, new-per-request transport, fixed output work, Benchmark/server token agreement, and TTFT/E2E SLOs. Its publication target pins mlx-lm 0.31.3, MLX 0.32.2, the Qwen3-0.6B-4bit snapshot, tokenizer, chat template, launcher, and the fixed `{"enable_thinking":false}` server argument.

Publication output contains Benchmark-owned request traces, raw SSE events and comments, raw trials, request/trial metrics, output hashes, host samples, process audit, attempt ledger, artifact manifest, and checksums. Engine summaries cannot satisfy a gate. HTTP/1.0 rows are absolute-only and cannot support paired favorable cross-engine claims, TurnVector qualification, or native-inference claims.
