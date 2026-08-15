# Benchmark Design

## Purpose

TurnVectorBenchmark is an independent verifier, not an implementation package
inside TurnVector. Its fixtures, oracle, protocol, and gates must stay fixed
while a TurnVector change is evaluated. A benchmark change and a runtime change
therefore have separate review and Git history.

The normative scope is
`expectations/turnvector-implementation-v1.json`. It is pinned to the exact
TurnVector revision from which the implementation contract was derived. The
benchmark scope is therefore independent of current implementation and harness
readiness: an unavailable native adapter remains a required lane with fixed
cases, matrices, gates, and evidence.

Harness readiness has only two meanings:

- `executable`: the checked-in entrypoint can produce evidence now;
- `contract_only`: the test contract is fixed, but its real subject adapter is
  not checked in yet.

`contract_only` never means optional, waived, passed, or outside the benchmark.
A complete implementation result requires every required lane to be executable
and to pass. Contract inspection only validates this expectation structure; it
never certifies TurnVector.

## Executable Lane: Scheduler Policy V1

`suites/scheduler-policy-v1.json` drives a stateful candidate through the JSONL
protocol in `DRIVER-PROTOCOL.md`. The benchmark owns:

1. fixed model weights, candidate facts, resource decisions, time, and receipts;
2. an exact rational Engine Service ledger oracle;
3. expected scheduling decisions and state transitions;
4. repeated-run comparison and evidence artifacts.

The candidate owns its scheduler state and returns a plan plus visible ledger
state. The benchmark calculates the same state independently and fails on the
first disagreement. The reference driver is a protocol example and harness
self-test. It is not a performance baseline or a substitute for any
contract-only lane.

Protocol v1 deliberately permits at most one Work Candidate per Model in one
Scheduling Snapshot. This isolates cross-Model selection semantics before a
later suite adds within-Model candidate formation and batching.

## Oracle Order

For each Scheduling Snapshot, the oracle applies the following order:

1. Remove candidates that lack capability authorization, resource safety,
   timing feasibility, or Turn Output Reservation.
2. Treat only Models with a remaining valid candidate as Runnable Models.
3. Align a newly Runnable Model to the current runnable virtual-service
   baseline. Idle time therefore creates no credit.
4. Calculate Latest Safe Start as timing obligation minus Engine Service bound
   minus Runtime Overhead bound.
5. If any valid candidate is urgent, select by earliest Latest Safe Start,
   then smallest Model Ledger, then stable Candidate ID.
6. Otherwise select by smallest Model Ledger, stable Model ID, then stable
   Candidate ID.
7. After the matching Turn Receipt, add actual Engine Service divided by Model
   Weight to the selected Model Ledger. Urgent service is not free.

All durations are integer microseconds. Virtual-service ledger values use
canonical rational strings such as `120/1` or `25/3`; binary floating-point
rounding cannot affect a decision or a replay hash.

## Fixed Scenarios

| Scenario | Contract under test |
|---|---|
| `weighted-service-1-to-3` | Two continuously Runnable Models receive exactly 1:3 Engine Service and finish with equal normalized ledgers. |
| `idle-reentry-no-credit` | An idle Model is aligned on re-entry and cannot consume a stored service windfall. |
| `urgency-after-safety` | An unsafe earlier candidate is removed, a safe urgent candidate may preempt fair order, its service remains charged, and an all-unsafe Snapshot returns no plan. |

Every scenario runs three times. Exact plan and receipt state must pass on every
turn, and the canonical plan-trace hash must match across repetitions.

## Evidence Contract

`report.json` is the authoritative result. `summary.md` is derived and must not
override it. `trace.jsonl` retains full expected and observed inputs, decisions,
ledger transitions, and failure location. Contract and conformance failures are
not discarded as outliers.

The manifest records the implementation expectation and source revision, lane,
suite, scenario, executable benchmark-source, and schema SHA-256 values,
benchmark version, driver protocol, claim scope, and all unevaluated required
lanes. Environment evidence records the
benchmark and target Git revisions and dirty state, the exact driver command,
hashes of driver files named by that command, Python/host identity, and timeout.
`SHA256SUMS` binds the emitted evidence files.

Generated evidence belongs outside Git or under ignored `.artifacts/`. Checked-in
files are the benchmark source, schemas, fixtures, tests, and documentation,
not the result of one machine run.

## Required Implementation Lanes

The expectation manifest defines the following required implementation surface.
The table is descriptive; the JSON manifest is authoritative.

| Lane | Current harness | Required implementation evidence |
|---|---|---|
| Core event replay | Contract only | Sequenced events, atomic failure, invariant/state hashes, cancellation races, and stale/duplicate/indeterminate results. |
| Scheduler policy | Executable | Independent selection oracle, exact Engine Service receipts, and deterministic replay. |
| Scheduler performance | Contract only | Release-mode Core-only decision latency and throughput over fixed Snapshot sizes, measured without adapter IPC. |
| Request serving lifecycle | Contract only | Acceptance/admission/materialization order, bounded output, backpressure, cancellation, disconnect, and terminal status. |
| MLX native correctness | Contract only | Owner thread/stream, cross-thread rejection, and Dense/MoE output/logits/KV parity over Decode B1/B4 and Prefill 64/256/1024. |
| Bounded Turn and FFI | Contract only | Bounded Decode/Prefill, synchronized cancellation/failure cleanup, exclusive safety points, and native-boundary correctness/latency. |
| Residency and Memory Governor | Contract only | Load/unload/reload/restore/reclaim, shared loads, leases, reservations, resource modes, pressure, and delayed reclaim. |
| Cross-model serving | Contract only | Long-Prefill/Decode interference, weighted Engine Service, TTFT/TPOT tails, throughput, output correctness, and finite progress. |
| Observability qualification | Contract only | Command-buffer attribution/status, telemetry off/on overhead, external calibration, and a qualified service label. |
| Persistence and recovery | Contract only | Strong snapshot identity, roundtrip parity, corruption/interruption/concurrency faults, authoritative control recovery, and no effect replay. |
| Protocol and Worker supervision | Contract only | Authenticated negotiation, one device owner, bounded IPC, Worker crash/timeout/malformed outcomes, and fresh restart. |
| Certification envelopes | Contract only | Exact Capability Key applicability, fail-closed uncertified work, bound quarantine, and explicit recertification. |

The native matrices are expectations even when MLX support is absent. The
benchmark does not simulate them and call that a pass; it reports that their
required real adapters and evidence are unavailable. No lane may widen a claim
to another hardware, OS, model, dependency, or Certification Envelope without
a separately fixed identity and evidence record.
