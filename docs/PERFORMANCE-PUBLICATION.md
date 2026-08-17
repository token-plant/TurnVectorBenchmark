# Performance Publication

## Purpose

`performance-publication-v1` defines reproducible performance workloads and
independently validates their evidence. It does not replace implementation
qualification and cannot produce `full_implementation_status: passed`.

The profile deliberately separates three decisions:

1. **Contract validity**: the artifact has the exact case plan, protocol,
   identities, raw trials, summaries, thresholds, and required files.
2. **Evidence publication**: environment admission and every evidence gate
   pass. Correctness, route purity, and output completeness are evidence gates.
3. **Performance promotion**: every promotion gate passes its threshold frozen
   before execution. A promotion failure remains publishable negative evidence
   when the evidence itself is valid.

## Workloads

The checked-in contract owns 11 lanes and 103 planned cases.

| Lane | Activation | Cases | Scope |
| --- | --- | ---: | --- |
| `online-serving-single-client` | core | 8 | Streaming completion, TTFT, TPOT, decode, process audit |
| `online-serving-load` | core | 8 | Closed/open-loop latency tails, queueing, throughput, goodput |
| `real-model-direct-performance` | core | 12 | Dense/MoE, 4/6-bit, prompt depth, direct generation |
| `long-context-prefill` | core | 16 | 1K/2K/4K/8K prefill and memory scaling |
| `startup-lifecycle` | core | 3 | Process-cold, model-warm, benchmark-warm stages |
| `prefill-interference` | core | 12 | Long Prefill against interactive Decode under two arrival modes |
| `prefix-cache-restart` | core | 4 | Fresh-process disk restore with output and KV identity |
| `prefix-cache-stress` | core | 2 | Overlapping writers and bounded eviction pressure |
| `batched-decode-performance` | core | 8 | Batch 1/2/4/8, policy comparison, full-cohort parity |
| `accelerated-generation` | capability-conditioned | 6 | Same-package direct denominator, fallback and route evidence |
| `embedding-ingest` | capability-conditioned | 24 | Pooling correctness and batch/chunk ingest scaling |

Capability-conditioned lanes may report `unsupported` with a concrete reason.
They cannot report a passing or publishable row without measured cases. A core
lane cannot be marked unsupported.

## Session And Claim Identity

Every evidence artifact contains exactly one lane and one session mode.
Absolute values from different modes are not comparable. A `paired_delta`
claim additionally binds a denominator digest, pairing-key digest, and
`same_session` measurement semantics. Separate serialized runs may be retained
for diagnosis but cannot be labeled as a paired delta.

The session binds:

- exact TurnVector and Benchmark revisions plus clean/dirty state;
- claim type and start/end timestamps;
- model, prompt, runtime, subject binary, and certification record digests;
- host identity and start/end conditions.

Host conditions record external power, thermal state, load average, top-process
CPU, and the result of a pre-run admission check. The contract freezes load
average and top-process CPU limits; the judge evaluates the recorded values
even when the artifact declares admission success. Host conditions, request
timestamps, and raw trials remain under Benchmark custody.

## Trial And Metric Contract

Each lane fixes warmups, measured repetitions, cooldown, process isolation,
execution order, cache policy, sampling policy, and workload constants. The
artifact must contain every expanded case in order and every repetition in
order. Missing, duplicate, reordered, or extra cells are contract failures.

Every trial contains the exact metric set for its lane. The judge recomputes
`median`, nearest-rank `p95`/`p99`, `min`, `max`, and `sum` values from raw
trials. A declared summary that differs from the recomputed value is rejected
before gates are evaluated.

Benchmark-contract thresholds are immutable correctness or completeness
values. Performance and environment-specific limits come from a hash-bound
certification record and are copied into `frozen_thresholds` before the first
measured case. The judge opens that record and checks its schema, contract and
lane identity, applicability at session start, runtime/binary/model/prompt
identity, and exact threshold equality. No threshold may be derived from the
samples it judges.

## Artifact Custody

Every supported lane requires these common files:

- `run_manifest`;
- `environment`;
- `raw_trials`;
- `host_samples`;
- `model_manifest`;
- `prompt_manifest`;
- `certification_record`.

Each lane adds workload-specific traces. Every descriptor is relative to the
evidence file, regular, non-symlinked, size checked, SHA256 checked, and marked
as Benchmark custody. Missing or extra descriptors fail the contract.

## Status Model

`validate-performance` reports independent evidence and promotion states:

| Evidence status | Meaning |
| --- | --- |
| `publishable` | Complete, admitted evidence with every evidence gate passing |
| `not_publishable` | Evidence gate, source identity, or host admission failed |
| `unsupported` | A capability-conditioned lane is explicitly unavailable |

`promotion_status` is `passed`, `failed`, `not_applicable`, or
`not_evaluated`. `publication_candidate` follows evidence validity, not whether
the measured performance was favorable. This preserves valid negative results
without presenting them as promoted performance.

The artifact declares its expected decision, but the judge derives the decision
again and rejects any disagreement.

## Commands

Inspect the frozen contract and expanded plan:

```bash
python3 -B -m turnvector_benchmark inspect-performance \
  --contract profiles/performance-publication-v1.json
```

Validate one evidence artifact and write a checksum-bound report:

```bash
python3 -B -m turnvector_benchmark validate-performance \
  --contract profiles/performance-publication-v1.json \
  --evidence /outside/git/performance-evidence.json \
  --output /outside/git/performance-validation
```

Exit code `0` means publishable evidence, `3` means not publishable, `4` means
explicitly unsupported, and `2` means the contract or artifact is malformed.

## Adapter Boundary

The contract, matrices, reducers, and judge are usable before a production
adapter exists. That does not make a performance claim. A claimable run still
requires Benchmark-owned clients and collectors plus an adapter that invokes
the real TurnVector runtime. Missing support stays explicit and fixture-derived
evidence remains non-product evidence.
