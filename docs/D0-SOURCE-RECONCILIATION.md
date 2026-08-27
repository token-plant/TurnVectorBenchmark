# D0 Source Reconciliation (Reviewed Human-Readable Semantic Matrix)

## Status and Purpose

- This document is the reviewed human-readable **D0 semantic matrix**, **not** the future canonical authority / `source-reconciliation-v1.json` runtime artifact.
- Filename substitution is insufficient: the reconciliation is semantic, not merely a path rename.
- Historical source revision: `7cbfe2caef3f2f9f95a03e17eb8741ed1acf98a2`.
- Paired current checkout: `eedad5faf881da329844463eeaf54d9970350abd`.
- TurnVector is **read-only** and must remain clean/unchanged.

## Reconciliation Matrix

| ADR | Historical path | Current path | Classification and semantic change | Exact affected lane IDs | Successor disposition |
| --- | --- | --- | --- | --- | --- |
| `0001` | `docs/adr/0001-use-exclusive-device-turns-for-the-mvp.md` | `docs/adr/0001-interleave-bounded-device-turns-for-the-mvp.md` | **material**: Exclusive single-Model turns become cooperative bounded interleaving under one lifecycle owner thread. | `cross-model-serving`; `mlx-native-correctness` | `replace_topology_obligations`: `cross-model-serving` keeps cases/matrices/gates/artifacts/claim scope and derives them from bounded interleaving; `mlx-native-correctness` also updates owner-thread meaning. |
| `0018` | `docs/adr/0018-default-the-experimental-mlx-backend-to-a-cpp-interface.md` | `docs/adr/0018-default-the-experimental-mlx-backend-to-an-in-process-cpp-interface.md` | **material**: A Worker/private-IPC backend becomes a statically linked in-process C++ Backend Interface. | `bounded-turn-and-ffi`; `mlx-native-correctness`; `certification-envelopes` | `replace_topology_obligations`: `bounded-turn-and-ffi` layer becomes `in-process-native-boundary`, `mlx-native-correctness` layer becomes `in-process-native-runtime`, certification identity meaning updates. |
| `0019` | `docs/adr/0019-run-a-rust-daemon-with-a-cpp-device-worker.md` | `docs/adr/0019-run-a-rust-daemon-with-an-in-process-cpp-device-runtime.md` | **material**: A separate C++ Worker becomes the same-process Rust daemon, Device Executor, and C++/MLX runtime. | `observability-qualification`; `bounded-turn-and-ffi`; `mlx-native-correctness`; `protocol-and-owner-lifecycle`; `certification-envelopes` | `replace_topology_obligations`: observability retains attribution/calibration coverage, native layers update, worker-supervision lane is renamed/reframed as owner lifecycle, certification identity meaning updates. |
| `0020` | `docs/adr/0020-version-and-serialize-the-private-backend-protocol.md` | `docs/adr/0020-use-a-narrow-in-process-backend-interface.md` | **material**: A framed private Backend Protocol becomes a narrow direct in-process operation family. | `mlx-native-correctness`; `protocol-and-owner-lifecycle`; `certification-envelopes` | `replace_topology_obligations`: remove private Worker protocol assumptions, retain local client protocol separately, update Backend Interface identity. |
| `0029` | `docs/adr/0029-supervise-one-device-owner-across-process-failure.md` | `docs/adr/0029-recover-one-device-owner-after-daemon-failure.md` | **material**: Worker lease/reclaim becomes process-wide daemon failure and next-daemon recovery with no distinct MLX process or device lease. | `protocol-and-owner-lifecycle` | `replace_topology_obligations`: preserve 24 CasePlans and five gates via same-process daemon/Device Executor fixture semantics. |
| `0030` | `docs/adr/0030-bound-ingress-and-keep-the-core-loop-nonblocking.md` | `docs/adr/0030-bound-ingress-and-keep-external-io-off-the-device-loop.md` | **scope_clarification**: Synchronous bounded Backend work may occupy the Device Executor, while socket, storage, audit, and all other external I/O remain off the Device Loop under scheduling-cut interference bounds. | `request-serving-lifecycle`; `protocol-and-owner-lifecycle` | `retain_with_scope_update`: `request-serving-lifecycle` changes only the source path and keeps cases/matrices/gates/layer/artifacts/claim scope; owner lifecycle adopts the clarified boundary. |
| `0035` | `docs/adr/0035-use-protobuf-with-independent-protocol-versions.md` | `docs/adr/0035-use-protobuf-for-versioned-local-client-protocols.md` | **material**: Protobuf versioning remains for local Data and Control clients and is explicitly decoupled from the in-process Backend Interface. | `protocol-and-owner-lifecycle` | `replace_topology_obligations`: protocol ID becomes `turnvector.benchmark.owner-lifecycle.v1` and coverage concerns local client protocol plus device-owner lifecycle, not private Backend IPC. |

## Successor Expectation Decisions

- Git-rename to `expectations/turnvector-implementation-v2.json`.
- ID: `turnvector-implementation-v2`.
- Schema: `turnvector.benchmark.expectation.v3`.
- Exact source reconciliation path + digest authority object.
- 12 lanes / 425 CasePlans / 58 gates.

## Validation Rules

- ADRs sorted / unique / exact.
- Paths normalized relative POSIX.
- Verify old blobs at old revision and current files at paired revision by SHA256.
- Exact classification / disposition enums.
- Exact affected-lane equality.
- Expectation / suite / schema / fixture wording changes together.
- Paired repo clean and unchanged before / after.
- Missing / extra / stale / dirty fails closed.

## Canonical JSON Record Note

The future canonical JSON record contains: old / current revision, path, digest, classification, affected lanes, disposition, bounded summary, and accepted design revision. This document alone **cannot** satisfy compile authority.
