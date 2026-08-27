# Benchmark Design

## Repository Boundary

TurnVectorBenchmark and TurnVector are independent repositories. This
repository owns contracts, matrices, lane-specific schemas, fixtures, oracles,
metric calculations, gates, and evidence formats. TurnVector may own thin
subject adapters, but they call actual production modules and cannot contain an
oracle or copied expected results.

A benchmark revision is fixed while a TurnVector candidate is qualified. Both
repositories' HEAD and `git status --short` are captured before and after the
run. Any change invalidates the complete evidence set.

## Deep Controller Interface

`LaneController` owns the full orchestration policy:

```text
Expectation + Suite + Candidate Certification Record
  -> CasePlan
  -> LaneRunner + Subject
  -> Raw Evidence
  -> Metrics
  -> Gates
  -> Reports
```

The controller uses a fixed registry of 12 `LaneRunner` objects. The CLI does
not dynamically discover plugins or implement lane behavior through a growing
conditional chain. Shared concerns such as subject lifecycle, timeouts,
artifact containment, certification applicability, gate evaluation, Git
identity, and checksums stay behind this interface.

The scheduler-only `BenchmarkRunner` and Driver Protocol v1 remain intact as a
compatibility surface. The new scheduler lane drives the same scenarios over
SubjectAdapter v1 and reuses the independent exact-rational `SchedulerOracle`.

## Expectation and Case Completeness

Expectation ID `turnvector-implementation-v2`, schema v3, defines the required
surface independently of current TurnVector support. Every lane is bound to:

- one versioned lane suite and protocol;
- one lane-specific strict case schema;
- the exact ordered matrix, behavior-case, and gate IDs from the expectation;
- raw observation sources and deterministic reducers;
- an adapter category and real execution boundary;
- required raw and derived artifacts.

The v3 expectation additionally binds the exact source-reconciliation authority
path and SHA-256, so a drift in the reconciled seven-source artifact fails
closed before the expectation can be used as authority.

The suite loader verifies exact equality with the expectation. A suite cannot
drop a matrix, dimension value, behavior case, or gate. The qualification plan
contains 425 matrix combinations (the owner-lifecycle lane preserves 24
CasePlans through six daemon outcomes by four client protocol relations, and
the certification identity matrix is the complete current 19-value x 5-state
applicability domain). Diagnostic-only cases, including synthetic zero KV, are
not counted as qualification combinations and cannot satisfy a qualification
gate.

`inspect` derives readiness from all suites, case schemas, the fixed runner
registry, and the ordered self-test gate coverage file. There is no writable or
manually asserted readiness status.

## Subject Boundary

SubjectAdapter v1 is strict JSONL:

```text
hello -> case_open -> case_step* -> case_close -> shutdown
```

The hello binds subject kind, immutable build identity, exact lane protocols,
binary hashes, dependencies, environment, and optional real Data Plane
descriptor. Missing support is `unsupported`, not skip or pass.

Case artifacts are written under a Benchmark-created root and returned as
relative path, byte size, and SHA256. The controller independently rejects path
escape, symlinks, special files, or identity mismatch. This keeps large logits,
KV, process samples, and traces off the control protocol.

Fixture subjects are structurally incapable of producing an implementation
claim. A report based on `kind: fixture` is always
`not_claimable_fixture`, regardless of gate results.

## Lane Judges

### Runtime Core

Core replay receives a Benchmark-generated, hash-bound pristine state and
ordered EffectResult/cancellation stream twice. The Benchmark derives receipt
applicability, whole-execution identity, atomic failure, invariant preservation,
Effect uniqueness, invalid duplicate/late/unknown/stale suppression, and
cancellation order from strict raw execution records. Scheduler policy independently computes each
selection and receipt ledger using exact fractions. Scheduler performance takes
Benchmark-generated Snapshots with exact rational ledger values and compares
the returned Plan to its own selection. Latency and throughput are complete
in-process Release Core samples after exactly 100 warmups and for at least 1000
monotonic-timed decisions; each measured decision has one sample and counted
operation. The JSONL process boundary is outside the measured region and
`driver_ipc_included` must be false.

### Native MLX

The native reference lock fixes:

- MLX `68cf2fddd8de5edd8ab3d926391772b2e2cedad8`;
- mlx-c `fba4470b89073180056c9ea46c443051375f7399`;
- `mlx==0.31.2` and `mlx-lm==0.31.3`;
- Dense model revision `73e3e38d981303bc594367cd910ea6eb48349da8`;
- MoE model revision `11aaad5b454a361ae33f19fb47b72bc74b3c3b55`;
- deterministic seed `20260812`.

`reference_oracle.py` loads the full pinned model and writes canonical output
token IDs, complete float32 logits, and every layer's key/value state. Decode
first evaluates a real Prefill for the requested 512, 2048, or 8192-token
context and proves the cache is nonzero. Candidate output is compared byte for
byte; the adapter never receives oracle bytes.

`export_cpp_direct_graph.py` exports all exact signatures needed by
`cpp_direct_oracle.cpp`. C++ primes Decode through a real Prefill outside the
measured region and invokes the imported graph with no Python in the measured
region. Compiled binaries, exported graphs, build caches, model weights, and raw
results are external hash-bound fixtures. A strict C++ Direct bundle manifest
binds the executable, exact MLX/mlx-c revisions, seed, graph paths, model shape
metadata, warmup, and sample count. The lane pairs each C++ Direct case with the
same candidate architecture/phase/batch/shape and compares full logits, full KV,
wall time, Engine Service samples, bounded Receipts, and cancellation cleanup.

### Real Serving and Lifecycle

Request lifecycle and cross-model serving require the subject hello to expose a
real Data Plane descriptor. The adapter may coordinate process lifecycle, but
production requests, cancellation, disconnect, backpressure, status, output,
TTFT/TPOT, throughput, and progress evidence are produced through
Benchmark-owned clients and collectors. Output sequence and capacity evidence
gates at-most-once publication and reservation safety.

The wire contract is `protocols/data_plane_v1.proto` plus the locked descriptor
set and SHA256 in `protocols/data-plane-v1.lock.json`. The Benchmark uses a
four-byte big-endian declared length and Protobuf frames, validates the declared
length before allocating the payload, negotiates the exact family/major/minor
and descriptor hash, and enforces directional limits and write timeouts.

### Memory and Observability

Memory qualification combines deterministic Governor event inputs with
Benchmark-owned process/system sampling. Reservation conservation, load leases,
Resource Mode order, process footprint, safety pressure, and Pending Reclaim
convergence are separate evidence streams. The subject cannot convert an
allocator report into host footprint or declare reclaim complete by itself.
Lease-protected unload, safety-filter-before-schedule, and reservation retention
until both process and backend reclaim are explicit gates. Benchmark samples
are retained as the Benchmark-owned `memory_samples` artifact.

Observability qualification runs telemetry off/on and coordinates xctrace
Instruments capture outside the subject. It pairs Turn identity with
CommandBuffer evidence, checks timestamp coverage and attribution, calculates
external calibration error percentiles, and calculates throughput/TPOT
overhead. A CommandBuffer service-time label is usable only if every
qualification gate passes.

### Recovery, Supervision, and Certification

Persistence cases receive a Benchmark-owned temporary runtime root. Faults are
introduced only through real files and process boundaries: corruption,
interrupted publication phases, concurrent access, process termination, and
restart. The judge compares snapshot/output/KV identities, authority state,
audit transitions, and external-effect replay.
Every target file is relative, regular, non-symlinked, and size/SHA256 checked
before mutation. Every signalled PID is rebound per case to an actual executable
whose hash appears in `hello_ack.binary_manifest`; this supports real daemon
restart without authorizing an arbitrary process. `fault_trace` is generated by
the Benchmark and conflicts with a subject artifact of the same ID.

The owner-lifecycle lane (`protocol-and-owner-lifecycle`) is activated only
through the benchmark fixture selection seam: the same-process
owner-lifecycle-device-executor-v1 fixture models exactly one daemon process
containing a fake Device Executor, never claims a real Backend Interface, and
never launches or describes a separate MLX Worker. Selecting it publishes
`benchmark_fixture` execution provenance before the first CasePlan START, so
the run is always `fixture_tainted` and `not_claimable_fixture` even when every
lane passes. The lane gates one device owner, no backend call before
initialization, no successful Receipt after daemon loss, bounded client
transport, and client-transport latency. Frame-size and latency limits come
only from the pre-run Certification Record. Fixture evidence is never
production evidence.

Until a production owner-lifecycle implementation replaces the fixture, the
same fixture selection applies in real, manual, and nightly runs, so those runs
collect nonclaimable structural evidence: `run_fixture_taint` is
`fixture_tainted`, `fixture_ids` is exactly
`owner-lifecycle-device-executor-v1`, and `full_implementation_status` is
`not_claimable_fixture` even when every lane passes. No current product claim
is possible.

Certification cases change model, phase, batch, shape, Adapter/MLX build,
Backend Interface revision, device, memory, OS, daemon build, bootstrap
manifest, capability descriptor, generation semantics, resource-signal
contract, and operation bound set identity. The judge requires exact record
applicability, immediate quarantine on a bound violation, and explicit
recertification. Expected applicability is derived from each CasePlan identity
mutation and record state; the subject cannot label its own record applicable.
Runtime probing cannot replace a missing certification record.

## Candidate Certification Record

All performance limits are fixed before execution. The record binds subject
build, SubjectAdapter protocol, exact environment identity, allowed matrix
values, and per-lane thresholds. The run manifest stores the resolved
threshold table before the first case.

Missing, build-mismatched, environment-mismatched, matrix-inapplicable,
threshold-incomplete, stale-evidence, quarantined, or superseded records are
`contract_failed`. No code path derives a limit from the samples being judged.
TurnVector Certification Records are immutable and carry no invented wall-clock
expiry: `issued_at` and `expires_at` are forbidden, and controller wall time is
provenance only, never record-applicability authority.

## External Fixture Manifest

The external fixture manifest binds the checked-in reference-lock SHA256 and an
ordered set of external files/directories. Before any implementation case, the
controller recursively inventories each directory, rejects symlinks and
special files, computes total bytes, hashes every file, and hashes the canonical
inventory. Missing inputs are `environment_unavailable`; identity differences
are contract failures.

## Status and Aggregation

Lane status is exactly one of:

- `passed`;
- `gate_failed`;
- `unsupported`;
- `environment_unavailable`;
- `contract_failed`;
- `infrastructure_failed`.

`run-all --profile qualification` executes independent lanes after failures.
Only one run with all 12 required lanes `passed`, a real implementation subject,
matching TurnVector source contract, and unchanged Git identities can emit
`full_implementation_status: passed`.

The current 425-case expectation has 58 ordered gates. The count is derived from
the expectation and must exactly match the checked-in per-gate failure fixture
coverage; it is not a manually asserted readiness flag.

Every failure retains the case plan, transcript/raw evidence available before
failure, metrics, gate details, failure records, environment, source and subject
identity, reports, and checksums. Human summaries may be derived later, but
`report.json` remains authoritative.

## Performance Publication Module

Performance publication is a separate module at the evidence-validation seam.
Its external interface is intentionally small:

```text
load contract -> inspect / expand case plan / validate evidence
```

Behind that interface the module owns strict contract parsing, matrix expansion,
session and comparison identity, host admission, protocol equality, raw-trial
completeness, summary recomputation, certification applicability, artifact
validation, gate evaluation, and the final publication decision. The CLI only
renders the returned result.

This separation prevents optional performance workloads from changing the 12
required implementation lanes or `full_implementation_status`. It also keeps
evidence validity separate from promotion: valid negative performance remains
publishable, while incorrect output, hidden route changes, dirty identity, or
failed host admission cannot become a publication candidate.

The checked-in performance contract has nine core lanes and two
capability-conditioned lanes. A capability-conditioned lane may be explicitly
`unsupported`; a core lane may not. Contract and judge availability before a
real adapter exists is not a product claim.

## Gateway Validation Module

Gateway validation is a separate Module at the optional network-edge evidence
Seam. It does not add a thirteenth required implementation lane. Its Interface
is `load -> inspect -> validate`; private implementations own fixed CasePlan
closure, source identity, raw artifact custody, lifecycle ordering, bounded-tail
gates, Unix-stage reduction, wire-byte accounting, and predicted reuse bounds.

The lifecycle judge consumes content-free observations from the real HTTP
Gateway, production Data Plane, daemon, and Backend lifecycle. A TurnVector
adapter may launch those processes but cannot provide pass/fail fields. The
Unix judge measures the current one-request-per-connection design only. Its
perfect-reuse result is a theory upper bound and cannot authorize pooling or
multiplexing.

The run manifest freezes the five lifecycle cases, 32 transport cells,
effective limits, balanced order, warmups, and repetitions before collection.
Both repositories must retain the same clean identities through the run.
Fixture evidence remains structurally non-claimable even when all gates pass.
