# Driver Protocol V1

## Transport

The benchmark starts one driver process per scenario repetition. Requests are
newline-delimited JSON on standard input; the driver emits exactly one JSON
object on standard output for each request. Diagnostic text belongs on standard
error. The default response deadline is five seconds and is a harness liveness
bound, not a scheduler performance measurement.

The lifecycle is:

```text
hello -> initialize -> (schedule -> [receipt])* -> shutdown
```

A `schedule` that returns `candidate_id: null` has no matching `receipt`.
Unknown fields, missing fields, malformed rational values, unexpected messages,
timeouts, early EOF, and nonzero driver exits are contract failures.

## Handshake

Request:

```json
{"kind":"hello","protocol_version":"turnvector.benchmark.driver.v1"}
```

Response:

```json
{
  "kind": "hello_ack",
  "protocol_version": "turnvector.benchmark.driver.v1",
  "driver_name": "turnvector-core-adapter",
  "driver_version": "<implementation identity>"
}
```

## Initialize

Request:

```json
{
  "kind": "initialize",
  "scenario_id": "weighted-service-1-to-3",
  "repetition": 0,
  "models": [
    {"model_id":"alpha","weight":1},
    {"model_id":"beta","weight":3}
  ]
}
```

The driver must reset all scenario state. Response:

```json
{
  "kind": "initialized",
  "scenario_id": "weighted-service-1-to-3",
  "model_ledgers_us": {"alpha":"0/1","beta":"0/1"}
}
```

## Schedule

Request:

```json
{
  "kind": "schedule",
  "sequence": 1,
  "now_us": 0,
  "resource_mode": "normal",
  "candidates": [
    {
      "candidate_id": "alpha.decode",
      "model_id": "alpha",
      "execution_phase": "decode",
      "service_class": "standard",
      "engine_service_bound_us": 120,
      "runtime_overhead_bound_us": 20,
      "timing_obligation_us": null,
      "capability_authorized": true,
      "resource_safe": true,
      "timing_feasible": true,
      "output_reserved": true
    }
  ]
}
```

`resource_mode` is evidence context. The benchmark fixture supplies the
authoritative `resource_safe` result; a scheduler driver must not second-guess
the Resource Governor. Likewise, capability, timing, and output feasibility are
explicit boundary facts.

Response:

```json
{
  "kind": "plan",
  "sequence": 1,
  "candidate_id": "alpha.decode",
  "runnable_ledgers_us": {"alpha":"0/1"}
}
```

`runnable_ledgers_us` is the state after Runnable-Model alignment and before
the selected Receipt is charged. It contains exactly the currently Runnable
Models. A no-plan response uses `candidate_id: null` and an empty ledger map
when no Model is Runnable.

## Receipt

Request:

```json
{
  "kind": "receipt",
  "sequence": 1,
  "candidate_id": "alpha.decode",
  "model_id": "alpha",
  "actual_engine_service_us": 120
}
```

Response:

```json
{
  "kind": "receipt_accepted",
  "sequence": 1,
  "model_ledgers_us": {"alpha":"120/1","beta":"0/1"}
}
```

The response contains every configured Model, including idle Models. A Receipt
must match the immediately preceding non-null plan exactly.

## Shutdown

Request and response:

```json
{"kind":"shutdown"}
{"kind":"shutdown_ack"}
```

After the acknowledgement the driver must exit successfully within the response
deadline.

## Integration Boundary

A TurnVector adapter should translate these benchmark messages into its public
pure-Core test seam. It must not copy expected decisions from scenario files,
read benchmark output, or call the benchmark oracle. The reference driver is
kept separate from the oracle implementation so CI exercises a real process and
wire boundary.
