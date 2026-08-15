# TurnVectorBenchmark

TurnVectorBenchmark is the paired benchmark project for validating changes to [TurnVector](https://github.com/token-plant/TurnVector).

This repository is intentionally versioned separately from TurnVector. TurnVector updates may run this benchmark, while benchmark source, baselines, fixtures, dependencies, and generated files remain unchanged. Benchmark changes are reviewed and released independently in this repository.

## Implementation Expectation

`expectations/turnvector-implementation-v1.json` is the normative benchmark
contract. It is derived from a fixed TurnVector implementation-design revision,
not from the subset that happens to run today. Its required lanes cover:

- deterministic Runtime Core replay, scheduler policy, and Core-only decision performance;
- bounded request and output lifecycle behavior;
- real MLX owner-thread execution, Dense/MoE output, logits, and KV parity;
- bounded Decode/Prefill Turns and native-boundary qualification;
- residency, resource modes, reservations, and observed reclaim;
- cross-model interference, serving tails, throughput, and progress;
- Metal observability qualification and telemetry overhead;
- persistence, recovery, protocol supervision, and scoped certification.

Every required lane must eventually execute and pass. A `contract_only` harness
is an unmet verification dependency, not optional or future scope. It keeps the
implementation expectation fixed before the corresponding adapter exists and
prevents a partial result from becoming a full implementation claim.

The currently executable `scheduler-policy` lane uses
`scheduler-policy-v1`. A candidate driver receives deterministic scheduling
inputs over JSONL and must match an independent oracle for:

- runnable-only weighted Engine Service accounting;
- idle-model re-entry without banked service credit;
- resource safety before urgency and fairness;
- Latest Safe Start urgency with normally charged Engine Service;
- stable tie-breaking, exact receipt accounting, and repeated replay.

Passing this lane establishes scheduler-policy conformance only. The resulting
report lists every other required lane as not evaluated.

## Run

Python 3.9 or newer is sufficient; the harness has no third-party packages.

```bash
python3 -B -m unittest discover -s tests -v

python3 -B -m turnvector_benchmark expectation \
  --manifest expectations/turnvector-implementation-v1.json \
  --target-repo /path/to/TurnVector

python3 -B -m turnvector_benchmark validate \
  --suite suites/scheduler-policy-v1.json

artifact_root="$(mktemp -d)"
python3 -B -m turnvector_benchmark run \
  --expectation expectations/turnvector-implementation-v1.json \
  --lane scheduler-policy \
  --suite suites/scheduler-policy-v1.json \
  --driver-command "python3 -B drivers/reference_driver.py" \
  --target-repo /path/to/TurnVector \
  --output "$artifact_root/reference"
```

Replace the reference driver command with a TurnVector-owned adapter to test an
implementation. The driver command and arguments are recorded in the artifact;
do not put credentials or secrets in them.

The `expectation` command validates and inspects the contract. Its exit code `0`
does not mean that TurnVector passed; inspect `full_run_available`,
`claim_status`, and the required-lane lists. For an executable lane, exit code
`0` means that lane's gates passed, `2` means an input or driver contract
failed, and `3` means the driver was protocol-valid but disagreed with the
oracle. Every executed lane run writes:

- `manifest.json`: expectation, lane, suite, input, source hashes, and claim scope;
- `environment.json`: host, Git, driver, and exact file identity;
- `trace.jsonl`: decision-by-decision expected and observed evidence;
- `report.json`: authoritative gate status and metrics;
- `summary.md`: derived human-readable status;
- `SHA256SUMS`: hashes for every artifact above.

See [Benchmark Design](docs/BENCHMARK-DESIGN.md) and
[Driver Protocol](docs/DRIVER-PROTOCOL.md) for the stable contract.
