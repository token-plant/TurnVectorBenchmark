# Gateway Validation

## Purpose

`turnvector-gateway-validation-v1` is the independent validation contract for
the optional TurnVector Compatibility Gateway. It answers two separate
questions:

1. can one request's Backend ownership end while its bounded HTTP response is
   still draining; and
2. what is the measured per-request Unix Data Plane setup cost before any
   ownership-changing optimization is considered.

This profile does not change the 12 required implementation-qualification
lanes or `full_implementation_status`. The Gateway is a separate network-edge
process and does not redefine Runtime Core, daemon, Backend, or Model Residency
qualification.

The canonical TurnVector source contract is
`turnvector.gateway-validation.v1`. The checked-in profile binds its relative
path and SHA-256, so a source-contract edit requires a new Benchmark contract
identity or an explicit profile update.

## Deep Module Interface

The Gateway validation Module exposes one Interface:

```text
load contract -> inspect exact CasePlan -> validate immutable evidence
```

Behind it, private Modules own:

- contract parsing and exact v1 closure;
- source-contract identity and artifact custody;
- response-lifecycle event judgment;
- Unix stage reduction and theoretical upper-bound calculation;
- report status and claim boundaries.

The CLI only renders the returned result. TurnVector adapters cannot supply
pass/fail decisions, computed summaries, or expected output.

## Fixed CasePlan

The lifecycle plan has five cases:

| Case | Purpose |
|---|---|
| `fast-fit` | normal non-streaming control path |
| `slow-fit` | release-before-close plus peer progress under a slow reader |
| `stalled-fit` | release-before-close plus peer progress when bounded output fits |
| `stalled-overflow` | write deadline, cancellation, release, close, and no leak |
| `disconnect-mid-stream` | cancellation/release without replay or transfer |

The Unix plan crosses:

- `kernel_uds` and `production_data_plane` probe paths;
- concurrency `1`, `8`, `32`, and `128`;
- minimum and profile-maximum wire classes;
- process-cold and process-warm states.

This produces 32 cells and 3,200 measured trials. Collection order alternates
the CasePlan forward and backward for each repetition. The judge rejects a
reordered, missing, duplicate, or extra trial.

## Evidence Layout

A run uses an output directory outside Git:

```text
gateway-evidence/
  evidence.json
  run-manifest.json
  raw/
    lifecycle.jsonl
    transport.jsonl
    host.jsonl
```

`evidence.json` binds both repository identities before and after collection,
all production build/profile/route/protocol identities, the effective limit
identity, the shared monotonic clock domain, environment, and each external
artifact's relative path, size, custody, and SHA-256. A calibrated multi-clock
mode needs a later contract with a hash-bound calibration artifact and reducer.

The run manifest freezes the exact lifecycle and Unix CasePlans, effective
limits, and transport protocol before measured trials. The judge then reads the
Benchmark-owned JSONL rather than accepting subject summaries.

The lifecycle trace contains content-free ordered events, zero-required safety
counters, and bounded queue observations. It contains no prompt, token,
response, credential, or principal data. The transport trace retains every
stage duration, request-variable duration, CPU time, context switches, frame
payload sizes, file-descriptor high-water mark, connection count, error count,
and first-response latency.

## Reduction and Claims

For every transport trial the judge derives:

```text
F = socket
  + connect_accept
  + peer_credential
  + Hello
  + descriptor_validation

C_one = F + request_variable
B_wire = sum(4 + protobuf_payload_bytes)
```

It retains min, nearest-rank p50/p95/p99, and max distributions. Before a reuse
candidate exists, the report computes only the perfect-reuse upper bound:

```text
F * (k - 1) / k, for k in {2, 8, 32}
```

That field is explicitly predicted. It is not measured savings, and it assumes
zero candidate overhead. The report never emits `pooling_qualified` or
authorizes an ownership change.

The lifecycle judge recomputes Backend, response-tail, and complete-response
durations from raw monotonic events. Slow and stalled-fit cases must also show a
second production request progressing after Backend release and before response
close. This prevents an apparent timestamp split from hiding global
serialization.

Status is:

- `publishable`: complete admitted real-system evidence and every gate passes;
- `not_publishable`: identity, custody, host, completeness, lifecycle, or Unix
  baseline evidence fails;
- `not_claimable_fixture`: all structural/gate checks pass, but the subject is
  a fixture.

Only `publishable` can set
`backend_response_lifetimes_decoupled=true` and
`per_request_uds_baseline_measured=true`. Every status keeps
`pooling_qualified=false` and `ownership_change_authorized=false`.

## Commands

Inspect the fixed plan:

```bash
python3 -B -m turnvector_benchmark inspect-gateway-validation \
  --contract profiles/gateway-validation-v1.json
```

When the paired TurnVector checkout contains the fixed source document, verify
it at inspection time:

```bash
python3 -B -m turnvector_benchmark inspect-gateway-validation \
  --contract profiles/gateway-validation-v1.json \
  --target-repo /path/to/TurnVector
```

Validate evidence and write the authoritative checksum-bound report:

```bash
python3 -B -m turnvector_benchmark validate-gateway-validation \
  --contract profiles/gateway-validation-v1.json \
  --evidence /outside/git/gateway-evidence/evidence.json \
  --output /outside/git/gateway-validation-report
```

Exit code `0` is publishable, `3` is not publishable, `4` is a valid but
non-claimable fixture, and `2` is a malformed contract or artifact.

## Current Limitation

The contract, strict judge, schemas, and self-tests are available before the
production Gateway exists. This is not a Gateway result. A claimable run still
requires Benchmark-owned HTTP clients and collectors, the real Gateway Unix
adapter, the production Data Plane and daemon, and unchanged source identities.

Version one measures one request per connection. If its perfect-reuse upper
bound exceeds a separately declared budget, a later paired contract may test a
pre-negotiated single-use pool. Multiplexing requires a new TurnVector ADR and
a new validation contract because it changes ownership, cancellation, failure,
and backpressure semantics.
