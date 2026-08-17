# SubjectAdapter Protocol V1

## Role

SubjectAdapter v1 is the narrow seam between the independent benchmark and the
TurnVector build under test. An implementation adapter invokes a real pure Core,
native Worker, or daemon/worker system. It does not contain a benchmark oracle,
read expected output, reduce metrics, choose thresholds, or manufacture a
production protocol result.

The benchmark launches one adapter process per lane. JSON objects are exchanged
one per line on standard input and output. Standard error is diagnostic and is
bounded to 64 KiB. Each request has exactly one response and all response fields
are strict. The command is an argv array from the subject manifest; no shell is
involved.

```text
hello -> case_open -> case_step* -> case_close -> shutdown
```

## Hello

Request:

```json
{
  "kind": "hello",
  "protocol_version": "turnvector.benchmark.subject.v1",
  "run_id": "<benchmark run identity>",
  "lane_id": "mlx-native-correctness",
  "lane_protocol": "turnvector.benchmark.mlx-native.v1"
}
```

Response:

```json
{
  "kind": "hello_ack",
  "protocol_version": "turnvector.benchmark.subject.v1",
  "subject": {
    "name": "turnvector-native-adapter",
    "version": "1.0.0",
    "kind": "implementation",
    "build_identity": "<immutable candidate build identity>"
  },
  "supported_lanes": {
    "mlx-native-correctness": "turnvector.benchmark.mlx-native.v1"
  },
  "binary_manifest": [
    {"path": "/absolute/path/to/adapter", "sha256": "<64 lowercase hex>"}
  ],
  "dependency_manifest": [
    {"name": "mlx", "version": "0.31.2", "identity": "<source/build identity>"}
  ],
  "environment_identity": {
    "device_class": "<exact device identity>",
    "memory_bytes": 274877906944,
    "os_build": "<exact OS build>"
  }
}
```

The controller independently reads and hashes every binary-manifest path. The
subject kind must match the subject manifest, so a fixture cannot relabel itself
as an implementation. A missing lane or different lane protocol is
`unsupported`.

System lanes whose execution boundary is `direct_data_plane` also return a
`data_plane` object containing the real production endpoint descriptor. The
adapter may manage process lifecycle, but benchmark-owned clients connect to
that endpoint directly. An implementation response without the descriptor is a
contract failure.

The descriptor is strict and contains exactly the locked protocol identity,
direct Unix socket, limits, timeouts, Dense/MoE model identities, and current
process IDs:

```json
{
  "protocol_family": "turnvector.data-plane",
  "protocol_major": 1,
  "protocol_minor": 1,
  "descriptor_sha256": "<locked 64 lowercase hex>",
  "transport": "unix_stream",
  "socket_path": "/absolute/direct/path/to/data-plane.sock",
  "limits": {
    "max_frame_bytes": 16777216,
    "max_outstanding_commands": 256,
    "max_command_bytes": 1048576
  },
  "timeouts": {
    "connect_seconds": 5,
    "frame_seconds": 120,
    "max_server_write_timeout_ms": 5000
  },
  "model_revisions": {"dense": "<64 hex>", "moe": "<64 hex>"},
  "process_ids": [1234, 1235]
}
```

The socket path must directly name a Unix socket. The Benchmark loads message
types from `protocols/data-plane-v1.pb`, negotiates the exact descriptor hash,
and validates the four-byte big-endian declared frame size before reading or
allocating the Protobuf payload.

## Case Open

Request:

```json
{
  "kind": "case_open",
  "run_id": "<run identity>",
  "case": {
    "case_id": "mlx-native-correctness.decode.0001",
    "lane_id": "mlx-native-correctness",
    "matrix_id": "decode",
    "ordinal": 1,
    "parameters": {
      "model_architecture": "dense",
      "batch_size": 1,
      "context_tokens": 512
    },
    "behavior_case_ids": ["owner-thread-stream"],
    "operations": ["generate-mlx-reference", "execute-native-turn"],
    "diagnostic_only": false
  },
  "artifact_root": "/benchmark/owned/temporary/root",
  "case_directory": "cases/0001"
}
```

Response status is `ready`, `unsupported`, or `environment_unavailable`:

```json
{"kind":"case_open_ack","case_id":"mlx-native-correctness.decode.0001","status":"ready"}
```

The benchmark creates the case directory before this request. The adapter must
write only beneath the supplied artifact root.

## Case Step

Request:

```json
{
  "kind": "case_step",
  "case_id": "mlx-native-correctness.decode.0001",
  "step_index": 1,
  "operation": "execute-native-turn",
  "payload": {
    "matrix_id": "decode",
    "parameters": {"model_architecture":"dense","batch_size":1,"context_tokens":512},
    "seed": 20260812
  }
}
```

Response:

```json
{
  "kind": "case_step_ack",
  "case_id": "mlx-native-correctness.decode.0001",
  "step_index": 1,
  "evidence": {"operation_id":"<subject operation identity>"}
}
```

Lane runners define the operation payload and evidence schema. Scheduler policy
uses explicit initialize, schedule, and receipt steps; the benchmark compares
every returned plan and exact rational ledger to its own oracle. Native
correctness gives the subject deterministic input identity but never its oracle
bytes.

For `core-event-replay`, both replay operations receive the same
`turnvector.benchmark.core-event-input.v1` value and SHA256. It contains the
pristine generation, pending/seen Effect identities, the ordered EffectResult,
and an optional cancellation event. Each response contains exactly one
`execution` with the input SHA256, final-state SHA256, receipt application and
commit flags, before/after state hashes, published Effect IDs and sequences, and
nullable cancellation commit/terminal sequences. The Benchmark derives
sequence applicability, atomic failure, Effect uniqueness, invalid-result
suppression, cancellation order, and whole-execution replay identity; the
adapter does not return invariant pass/fail values.

For `scheduler-performance`, `measure-release-core` receives four
Benchmark-generated Snapshots, exact rational ledger strings, their SHA256, and
a fixed Release/warmup/iteration measurement contract. The implementation
returns only the same input SHA256, one observed candidate ID per Snapshot,
complete latency samples, operation/seconds totals, and whether JSONL IPC was in
the measured region. Its strict measurement identity must state Release mode,
100 warmup decisions, at least 1000 measured decisions, a monotonic timer, and
the `scheduler_core_only` measured region. There must be exactly one latency
sample and one counted operation per measured decision. Benchmark computes
every expected Plan and owns `plan_trace`, `oracle_trace`, `latency_samples`, and
`measurement_trace`.

For `bounded-turn-and-ffi`, the `cpp-direct-build` external artifact is a
directory containing `manifest.json` governed by
`schemas/cpp-direct-bundle-v1.schema.json`. The benchmark invokes that binary
directly for every `cpp_direct_oracle` case. For each paired
`implementation_candidate` case, the subject returns exactly these artifact
IDs:

- `candidate_logits`: raw contiguous little-endian float32 logits;
- `candidate_kv`: raw per-layer key/value float32 bytes in graph output order;
- `candidate_latency`: ASCII CSV with
  `iteration,wall_us,engine_service_us`;
- `turn_receipt`: strict `turnvector.benchmark.turn-receipt-evidence.v1` JSON;
- `candidate_cleanup`: strict
  `turnvector.benchmark.native-cleanup-evidence.v1` JSON.

The Receipt must contain bounded `completed` and `cancelled` outcomes. The
cleanup trace must include the cancellation outcome and an explicit boolean for
every cleanup result. The benchmark pairs cases by architecture, phase, batch,
and shape; hashes logits and KV itself; and derives latency regression from the
two complete sample series. Python exports the graph before qualification but
is absent from the C++ Direct measured region.

For `persistence-and-recovery`, `stage-runtime-root` returns one strict `stage`
object with `process_id`, absolute `process_executable`, `process_sha256`, and
nullable `fault_target`, `replacement`, and `phase_marker` file descriptors.
File descriptors contain relative `path`, `size`, `sha256`, and the required
fault-specific `role`. The executable must be present in
`hello_ack.binary_manifest`. Benchmark then performs the real atomic replace,
byte corruption, journal truncation, duplicate append, concurrent read/write,
or phase-bound `SIGTERM`; the adapter only acknowledges the resulting custody
record and restarts/inspects production state. The Benchmark owns `fault_trace`.

For Worker supervision, each operation payload includes a hash-bound
`turnvector.benchmark.worker-fixture.v1` command prefix. The adapter appends the
real production Worker command and lets the real daemon supervise the proxy.
The proxy owns only deterministic normal/crash/timeout/malformed/incompatible/
duplicate-frame injection; it contains no production protocol decision.

## Case Close and Artifacts

Response:

```json
{
  "kind": "case_close_ack",
  "case_id": "mlx-native-correctness.decode.0001",
  "observations": {},
  "artifacts": [
    {
      "id": "candidate_logits",
      "path": "cases/0001/complete-logits.bin",
      "size": 608512,
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

All paths are relative to `artifact_root`. Absolute paths, parent traversal,
symlinks, non-regular files, size mismatch, or hash mismatch are contract
failures. Large logits, KV, process traces, and Instruments output therefore do
not cross JSONL.

An MLX implementation case returns exactly `candidate_output`,
`candidate_logits`, `candidate_kv`, and `owner_thread_trace`. The owner trace is
JSONL with `operation_id`, `owner_thread_id`, `execution_thread_id`,
`cross_thread_attempt`, and `accepted` on every record. Each case includes a
normal owner-thread operation and a rejected cross-thread attempt. The
benchmark computes the violation count from those records; it does not accept a
subject-provided count.

The benchmark computes metrics from observations and independently captured
evidence. For MLX correctness it compares candidate files to its Python MLX-LM
oracle byte for byte. For performance it computes the fixed percentile or rate
from complete samples. Subject-provided pass/fail labels are not accepted.

Memory implementation cases must expose the real Data Plane process IDs. The
Benchmark samples their aggregate RSS and system pressure during each case and
owns `memory_samples`; Governor decisions still contain raw lease, Resource
Mode, reservation, and Pending Reclaim events for independent rule reduction.

## Shutdown

```json
{"kind":"shutdown"}
{"kind":"shutdown_ack"}
```

The process must then exit zero within the adapter timeout. Timeout, early EOF,
malformed JSON, extra or missing fields, and nonzero exit are
`contract_failed`; benchmark process and collector failures are
`infrastructure_failed`.

## Fixture Identity

`drivers/reference_subject.py` is a judge self-test fixture. Its hello declares
`kind: fixture`; even when every gate passes, the aggregate report is
`not_claimable_fixture`. Its `--fail-gate` mode supplies one incorrect fixture
for every gate so CI proves that each judge rejects its target failure.

The reference fixture is not an adapter template for TurnVector. A TurnVector
adapter must remain thin and call production seams; copying fixture observations
or expected values is a benchmark contract violation.
