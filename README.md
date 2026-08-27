# TurnVectorBenchmark

TurnVectorBenchmark is the independently versioned qualification and
performance-evidence repository for
[TurnVector](https://github.com/token-plant/TurnVector). It owns the expectation,
case matrices, fixtures, independent oracles, metric reduction, gates, and
evidence format. TurnVector owns only adapters that invoke real production
modules or expose the real daemon/Device Executor system under test.

The benchmark never treats an absent implementation as a pass. A required lane
with no compatible adapter is `unsupported`, and a complete implementation
claim requires all 12 required lanes to be `passed` in one valid run.

## Qualification Contract

`expectations/turnvector-implementation-v2.json` is the normative contract. Its
ID remains `turnvector-implementation-v2`; schema v3 binds every lane to a
versioned suite, strict case schema, and a runner in the fixed registry, and
binds the exact hash of the source-reconciliation authority artifact.
Readiness is derived by `inspect` from those bindings and the checked-in gate
self-tests. There is no manually maintained harness-readiness flag. The v1
expectation bytes remain only in Git history; they are never an active
contract.

The qualification profile expands 425 matrix combinations:

- deterministic Core replay, exact scheduler policy, and in-process Release Core performance;
- request lifecycle, cancellation, disconnect, backpressure, and at-most-once output;
- Dense/MoE MLX output, complete-logits, and per-layer KV parity;
- bounded Decode/Prefill and the C++ Direct/in-process native boundary;
- residency, reservations, process footprint, pressure, and Pending Reclaim;
- cross-model timing, throughput, weighted service, progress, and output correctness;
- telemetry off/on, CommandBuffer attribution, Instruments calibration, and overhead;
- persistence faults, restart recovery, daemon/Device Executor owner lifecycle, and certification applicability.

Decode correctness, FFI, and observability cover context lengths 512, 2048, and
8192. Qualification Decode uses nonzero KV produced by a real Prefill. Synthetic
zero KV remains an explicitly diagnostic-only oracle mode.

## Execution Model

The fixed flow is:

```text
Expectation + Lane Suite + Candidate Certification Record
  -> CasePlan
  -> Benchmark Oracle / SubjectAdapter
  -> Raw Evidence
  -> Benchmark Metric Reduction
  -> Gates
  -> Lane and Aggregate Reports
```

`LaneController` is the single orchestration interface. It selects one of 12
fixed `LaneRunner` instances; there is no dynamic plugin discovery and no
12-branch CLI dispatcher. Subject processes use `SubjectAdapter v1`:

```text
hello -> case_open -> case_step* -> case_close -> shutdown
```

The hello response binds the subject build, supported lane protocols, binary
hashes, dependencies, and environment identity. Subject artifacts use paths
relative to a benchmark-owned temporary root; the controller rejects absolute
paths, `..`, symlinks, size differences, and SHA256 differences.

For real system lanes, an adapter may start, stop, or configure the subject but
cannot replace the production Data Plane. Its hello must expose the real Data
Plane descriptor, and benchmark-owned clients and collectors remain the source
of request, process, memory, and trace evidence. Native correctness is compared
against the checked-in MLX oracle, not an answer copied into a TurnVector
adapter.

The production Data Plane wire contract is the checked-in Protobuf source plus
its locked descriptor set. The client rejects unknown framing, a descriptor
hash mismatch, oversized declared frames, invalid negotiation, and causal or
sequence violations before lane metrics are reduced.

See [SubjectAdapter Protocol](docs/SUBJECT-PROTOCOL.md),
[Benchmark Design](docs/BENCHMARK-DESIGN.md), and the legacy
[Scheduler Driver Protocol](docs/DRIVER-PROTOCOL.md).

The future AX Engine comparison is grounded by two non-measurement analytic
references: [V1 definitions and baseline equations](docs/AX-TURNVECTOR-SCHEDULER-THEORY-V1.md)
and [V2 feasible regions, bounds, and critical states](docs/AX-TURNVECTOR-SCHEDULER-THEORY-V2.md).
They define benchmark inputs and invariants but contain no product benchmark
result.

## Run

The benchmark requires Python 3.9 or newer and its locked protobuf runtime.
Development and CI also install the locked descriptor generator.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt

python3 -B -m unittest discover -s tests -v

python3 -B -m turnvector_benchmark inspect \
  --expectation expectations/turnvector-implementation-v2.json \
  --target-repo /path/to/TurnVector
```

Run all 425 combinations against the non-claimable judge fixture:

```bash
artifact_root="$(mktemp -d)"
python3 -B -m turnvector_benchmark run-all \
  --profile qualification \
  --expectation expectations/turnvector-implementation-v2.json \
  --subject-manifest subjects/reference-fixture-v1.json \
  --certification-record certification/reference-fixture-v1.json \
  --output "$artifact_root/reference-fixture"
```

This must report 12 passed lanes and 425 executed cases, but its
`full_implementation_status` is always `not_claimable_fixture`.

A real qualification run also supplies the TurnVector checkout and the
pre-frozen external fixture manifest:

```bash
python3 -B -m turnvector_benchmark run-all \
  --profile qualification \
  --expectation expectations/turnvector-implementation-v2.json \
  --subject-manifest /path/to/turnvector-subject.json \
  --certification-record /path/to/candidate-certification.json \
  --external-fixtures /path/to/external-fixtures.json \
  --target-repo /path/to/TurnVector \
  --output /outside/git/qualification-evidence
```

Until a production owner-lifecycle implementation replaces the fixture, the
owner-lifecycle lane is fixture-selected in real, manual, and nightly runs too,
so those runs collect nonclaimable structural evidence rather than a product
claim: `run_fixture_taint` is `fixture_tainted`, `fixture_ids` is exactly
`["owner-lifecycle-device-executor-v1"]`, and `full_implementation_status` is
`not_claimable_fixture` even when every lane passes.

`run-lane` accepts the same arguments plus `--lane`. It produces a partial lane
result and never a complete implementation claim.

The legacy scheduler command remains supported:

```bash
python3 -B -m turnvector_benchmark run \
  --expectation expectations/turnvector-implementation-v2.json \
  --lane scheduler-policy \
  --suite suites/scheduler-policy-v1.json \
  --driver-command "python3 -B drivers/reference_driver.py" \
  --output /outside/git/legacy-scheduler-evidence
```

## Status and Evidence

Lane status is one of `passed`, `gate_failed`, `unsupported`,
`environment_unavailable`, `contract_failed`, or `infrastructure_failed`.
`run-all` does not stop independent lanes after a failure.

Every run writes a top-level manifest, environment, report, and `SHA256SUMS`.
Each lane writes its own case plan, manifest, environment, subject transcript,
raw evidence, metrics, gates, failures, report, and checksums. Failed evidence
is retained. Thresholds from the Candidate Certification Record are resolved
and frozen in the run manifest before case execution; a missing or
inapplicable record is a required failure. TurnVector Certification
Records are immutable and carry no invented wall-clock expiry: controller
wall time is provenance only and never record-applicability authority.

The current expectation contains 58 independently self-tested gates. Scheduler
performance Plan expectations, direct serving timing/output, host memory
samples, Instruments custody, and persistence fault custody are generated by
the Benchmark rather than accepted as subject pass/fail claims.

## Performance Publication Profile

Implementation qualification and performance publication are separate
decisions. The required `qualification` profile remains fixed at 12 lanes, 425
cases, and 58 gates. The additional
`profiles/performance-publication-v1.json` contract defines 11 performance
workloads, 103 planned cases, 66 metrics, and 54 evidence/promotion gates.

The performance contract covers isolated and loaded serving, direct generation,
long-context Prefill, startup stages, Prefill interference, persistent prefix
state, batched Decode, and capability-conditioned accelerated generation and
embedding ingest. It fixes session identity, warmups, repetitions, cooldown,
process/cache isolation, host admission, raw trials, artifact custody, summary
reducers, and claim boundaries before execution.

Inspect the plan:

```bash
python3 -B -m turnvector_benchmark inspect-performance \
  --contract profiles/performance-publication-v1.json
```

Validate a real evidence artifact and optionally write a checksum-bound report:

```bash
python3 -B -m turnvector_benchmark validate-performance \
  --contract profiles/performance-publication-v1.json \
  --evidence /outside/git/performance-evidence.json \
  --output /outside/git/performance-validation
```

The judge recomputes every summary from raw trials. Correctness and evidence
failures make a result `not_publishable`; a performance threshold miss remains
publishable negative evidence with `promotion_status: failed`. Unsupported
capabilities remain explicit and cannot produce a passing row. Add
`--require-promotion` when the command is being used as a release gate; a
publishable result with failed promotion gates then exits with code `5`. See
[Performance Publication](docs/PERFORMANCE-PUBLICATION.md).

The controller records both repositories' HEAD and `git status --short` before
and after the run. A change in either identity invalidates the evidence. Model
weights, MLX/mlx-c source and build caches, and raw Instruments traces remain
outside Git and are recursively size/hash checked through the external fixture
manifest.

## Gateway Validation Profile

The separate `profiles/gateway-validation-v1.json` contract fixes five
response-lifecycle cases and 32 Unix connection-cost cells without changing the
12 required implementation lanes. Its judge recomputes lifecycle durations,
peer progress, bounded failure outcomes, Unix setup stages, wire bytes,
distributions, and perfect-reuse upper bounds from Benchmark-owned raw JSONL.

```bash
python3 -B -m turnvector_benchmark inspect-gateway-validation \
  --contract profiles/gateway-validation-v1.json
```

A passing fixture remains `not_claimable_fixture`; no v1 result authorizes
pooling or multiplexing. See [Gateway Validation](docs/GATEWAY-VALIDATION.md).

## Native Oracle Lock

`oracles/mlx/reference-lock-v1.json` fixes seed `20260812`, MLX and mlx-c source
revisions, `mlx==0.31.2`, `mlx-lm==0.31.3`, and the Dense/MoE model revisions.
`reference_oracle.py` emits canonical output, complete-logits, and full layer-KV
bytes. `cpp_direct_oracle.cpp` measures an imported full-model graph without
Python in the measurement region. Weights, exported graphs, compiled binaries,
and raw results are external artifacts, not repository content.
