# TurnVectorBenchmark

TurnVectorBenchmark is the independently versioned qualification repository for
[TurnVector](https://github.com/token-plant/TurnVector). It owns the expectation,
case matrices, fixtures, independent oracles, metric reduction, gates, and
evidence format. TurnVector owns only adapters that invoke real production
modules or expose the real daemon/worker system under test.

The benchmark never treats an absent implementation as a pass. A required lane
with no compatible adapter is `unsupported`, and a complete implementation
claim requires all 12 required lanes to be `passed` in one valid run.

## Qualification Contract

`expectations/turnvector-implementation-v1.json` is the normative contract. Its
ID remains `turnvector-implementation-v1`; schema v2 binds every lane to a
versioned suite, strict case schema, and a runner in the fixed registry.
Readiness is derived by `inspect` from those bindings and the checked-in gate
self-tests. There is no manually maintained harness-readiness flag.

The qualification profile expands 385 matrix combinations:

- deterministic Core replay, exact scheduler policy, and in-process Release Core performance;
- request lifecycle, cancellation, disconnect, backpressure, and at-most-once output;
- Dense/MoE MLX output, complete-logits, and per-layer KV parity;
- bounded Decode/Prefill and the C++ Direct/native candidate boundary;
- residency, reservations, process footprint, pressure, and Pending Reclaim;
- cross-model timing, throughput, weighted service, progress, and output correctness;
- telemetry off/on, CommandBuffer attribution, Instruments calibration, and overhead;
- persistence faults, restart recovery, Worker supervision, and certification applicability.

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

## Run

The benchmark requires Python 3.9 or newer and its locked protobuf runtime.
Development and CI also install the locked descriptor generator.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt

python3 -B -m unittest discover -s tests -v

python3 -B -m turnvector_benchmark inspect \
  --expectation expectations/turnvector-implementation-v1.json \
  --target-repo /path/to/TurnVector
```

Run all 385 combinations against the non-claimable judge fixture:

```bash
artifact_root="$(mktemp -d)"
python3 -B -m turnvector_benchmark run-all \
  --profile qualification \
  --expectation expectations/turnvector-implementation-v1.json \
  --subject-manifest subjects/reference-fixture-v1.json \
  --certification-record certification/reference-fixture-v1.json \
  --output "$artifact_root/reference-fixture"
```

This must report 12 passed lanes and 385 executed cases, but its
`full_implementation_status` is always `not_claimable_fixture`.

A real qualification run also supplies the TurnVector checkout and the
pre-frozen external fixture manifest:

```bash
python3 -B -m turnvector_benchmark run-all \
  --profile qualification \
  --expectation expectations/turnvector-implementation-v1.json \
  --subject-manifest /path/to/turnvector-subject.json \
  --certification-record /path/to/candidate-certification.json \
  --external-fixtures /path/to/external-fixtures.json \
  --target-repo /path/to/TurnVector \
  --output /outside/git/qualification-evidence
```

`run-lane` accepts the same arguments plus `--lane`. It produces a partial lane
result and never a complete implementation claim.

The legacy scheduler command remains supported:

```bash
python3 -B -m turnvector_benchmark run \
  --expectation expectations/turnvector-implementation-v1.json \
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
and frozen in the run manifest before case execution; a missing, expired, or
inapplicable record is a required failure.

The current expectation contains 58 independently self-tested gates. Scheduler
performance Plan expectations, direct serving timing/output, host memory
samples, Instruments custody, and persistence fault custody are generated by
the Benchmark rather than accepted as subject pass/fail claims.

The controller records both repositories' HEAD and `git status --short` before
and after the run. A change in either identity invalidates the evidence. Model
weights, MLX/mlx-c source and build caches, and raw Instruments traces remain
outside Git and are recursively size/hash checked through the external fixture
manifest.

## Native Oracle Lock

`oracles/mlx/reference-lock-v1.json` fixes seed `20260812`, MLX and mlx-c source
revisions, `mlx==0.31.2`, `mlx-lm==0.31.3`, and the Dense/MoE model revisions.
`reference_oracle.py` emits canonical output, complete-logits, and full layer-KV
bytes. `cpp_direct_oracle.cpp` measures an imported full-model graph without
Python in the measurement region. Weights, exported graphs, compiled binaries,
and raw results are external artifacts, not repository content.
