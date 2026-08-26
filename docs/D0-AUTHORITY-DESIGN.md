# D0 Authority Design

**Status: accepted implementation design, not implemented.** Scope is
TurnVectorBenchmark only; TurnVector is read-only. This document is design
authority, not evidence of implementation: it carries no product,
performance, adapter-ready, executable-ready, or claim-ready evidence.

## Review Provenance

The hashes below are review provenance for traceability; they are not runtime
inputs and are never fed to a compiler or gate.

| Kind | sha256 |
| --- | --- |
| Parent design | `c345fc4816b4fef860161490347f8338d5a02b7866c609b978c8d22bd55e9ae5` |
| Parent accepted round | `5048dc4c8a264a62cf1505560f2e7e1d027cc3bb9b6c19a068df807821e847dd` |
| D0 implementation design | `3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a` |
| D0 accepted round | `ba46fbffcf2a8794cadffd9bb7174c961e321fb4876c61e9f2ec185457f059cc` |

## D0 Fixed Outcomes

D0 semantically reconciles seven stale ADR anchors. This is a semantic
reconciliation, not filename substitution: anchors are reconciled to their
meaning, and no file is renamed to dodge an identity check. The active PR 1
expectation remains `turnvector-implementation-v1`.
`turnvector-implementation-v2` is the accepted future successor; PR 4 may
activate it only after PR 3 installs the absorbing fixture-taint interlock.

Fixed counts are exact and contract-bound:

| Fixed outcome | Value |
| --- | --- |
| Lanes | exactly 12 |
| CasePlans | 425 (expanded from 385 by the complete current Certification identity matrix) |
| Gates | 58 |
| Obligations | 46, one judge each |
| Judges | 46 (exactly one judge contract per required obligation) |
| EvidenceBundleContracts | 12, one per lane |
| Distinct raw evidence-source IDs | 41 |
| Lane-context memberships | 21, across two context types |
| Post-gate-output memberships | 22, across two output types |
| Judge-semantic negative-test templates | 46 |
| Aggregate gate-semantic templates | 58 |
| Gate-plumbing templates | 58 |

The obligation/gate relation is an explicit lane-local many-to-many relation:
every required obligation and every gate has at least one incident pair, and
every expanded CasePlan in a lane obtains one enforcement path for every
accepted obligation-gate pair in that lane, using the obligation's independent
judge, the lane EvidenceBundleContract, and the gate. Exact ownership is
deferred to the catalog-content gate. No required obligation, case, gate,
matrix identity, or artifact is silently removed.

## Deep Ownership

LaneController remains the sole orchestrator. TurnVectorBenchmark owns SourceReconciliation,
ObligationCatalog, the private CoverageCompiler, and CompileCustody. AX contributes harness
mechanics only; it never provides TurnVector qualification authority, thresholds, baselines, or
evidence. StructuralFixturePass never implies ClaimPass.

## Seven-Source Reconciliation Decisions

The successor contract records all seven mappings and their historical/current
content digests. Classifications are fixed as follows:

- **0001: material replacement.** Exclusive single-Model turns become
  cooperative bounded interleaving under one lifecycle owner thread.
- **0018: material replacement.** A Worker/private-IPC backend becomes a
  statically linked in-process C++ Backend Interface.
- **0019: material replacement.** A separate C++ Worker becomes the same-process
  Rust daemon, Device Executor, and C++/MLX runtime.
- **0020: material replacement.** A framed private Backend Protocol becomes a
  narrow direct in-process operation family.
- **0029: material replacement.** Worker lease/reclaim becomes process-wide
  daemon failure and next-daemon recovery with no distinct MLX process or
  device lease.
- **0030: scope clarification with material wording updates.** Synchronous
  bounded Backend work may occupy the Device Executor, while socket, storage,
  audit, and all other external I/O remain off the Device Loop under
  scheduling-cut interference bounds.
- **0035: material replacement.** Protobuf versioning remains for local Data and
  Control clients and is explicitly decoupled from the in-process Backend
  Interface.

The `affected_lane_ids` sets are exact:

- 0001 maps to `cross-model-serving` and `mlx-native-correctness`.
- 0018 maps to `bounded-turn-and-ffi`, `mlx-native-correctness`, and
  `certification-envelopes`.
- 0019 maps to `observability-qualification`, `bounded-turn-and-ffi`,
  `mlx-native-correctness`, `protocol-and-owner-lifecycle`, and
  `certification-envelopes`.
- 0020 maps to `mlx-native-correctness`, `protocol-and-owner-lifecycle`, and
  `certification-envelopes`.
- 0029 maps to `protocol-and-owner-lifecycle`.
- 0030 maps to `request-serving-lifecycle` and `protocol-and-owner-lifecycle`.
- 0035 maps to `protocol-and-owner-lifecycle`.

`certification-envelopes` already cites current CONTEXT/ADR 0007/0008/0017/0031
directly, but the 0018/0019/0020 topology replacements determine its Adapter
build and Backend Interface identity meanings and therefore affect it
semantically.

### Source-Reconciliation-V1 Strict Record Contract

- **Path:** `authority/source-reconciliation-v1.json`.
- **Schema:** `turnvector.benchmark.source-reconciliation.v1`.
- **Strict fields:** `schema_version`, `id`, `predecessor_expectation`,
  `successor_expectation`, `target_source`, `mappings`, `design_gate_revision`.
- `predecessor_expectation` fields: `id`, `source_revision`, `exact_file_sha256`.
- `successor_expectation` fields: `id` and `schema_version` only. It does not
  contain the successor file digest, avoiding a cycle.
- `target_source` fields: `repository`, `revision`, `clean_required=true`.
- Each mapping has `adr_number`, `old_revision`, `old_path`, `old_sha256`,
  `current_revision`, `current_path`, `current_sha256`, `classification`,
  `affected_lane_ids`, `disposition`, and a bounded summary.
- `classification` is `material` or `scope_clarification`. `disposition` is
  `replace_topology_obligations` or `retain_with_scope_update`.
- Records are sorted by four-digit `adr_number`, paths are normalized
  repository-relative POSIX paths, and the seven ADR numbers are exact and
  unique.
- `design_gate_revision` is this proposal revision. The implementation
  substitutes the computed digest only after this bundle is frozen and passed;
  that receipt-only substitution is mechanical and creates no new decision.

## Versioned Expectation Decision

- Git-renamed path: `expectations/turnvector-implementation-v2.json`. The v1
  bytes remain in Git history and are not copied into the current tree. A full
  copied v1 file would violate the 500-line commit cap without adding runtime
  authority.
- New ID: `turnvector-implementation-v2`.
- New schema: `turnvector.benchmark.expectation.v3`, with the existing v2 fields
  plus one strict authority object containing `source_reconciliation_path` and
  `source_reconciliation_sha256`. The expectation does not name the catalog or
  traceability digests, avoiding cycles.
- `source_contract` revision becomes `eedad5faf881da329844463eeaf54d9970350abd`
  for the paired source checkout. `clean_required` remains true. Descendants
  remain acceptable to `inspect_source_contract`, while catalog file/section
  digests detect semantic source drift.
- Required lane and gate counts remain exactly 12 and 58. The complete current
  Certification identity matrix expands the exact CasePlan count from 385 to
  425. No required obligation, case, gate, matrix identity, or artifact is
  silently removed.

## Seven Successor Lane Dispositions

1. **request-serving-lifecycle** updates only ADR 0030's path. Existing cases,
   matrices, gates, layer, artifacts, and claim scope remain.
2. **cross-model-serving** updates only ADR 0001's path. Existing cases,
   matrices, gates, artifacts, and claim scope remain and now derive from
   bounded interleaving.
3. **observability-qualification** updates ADR 0019's path. Existing cases,
   matrices, gates, artifacts, layer, and claim scope remain because they
   concern attribution and calibration rather than Worker IPC.
4. **bounded-turn-and-ffi** updates ADRs 0018 and 0019 and changes layer
   `cpp-worker` to `in-process-native-boundary`. Existing cases, matrices,
   gates, artifacts, and claim scopes remain.
5. **mlx-native-correctness** updates ADRs 0001, 0018, 0019, and 0020; changes
   layer `cpp-worker` to `in-process-native-runtime`; and changes the
   owner-thread case wording from dedicated Worker owner thread to Device
   Executor lifecycle owner thread. Other cases, matrices, gates, artifacts,
   and claim scope remain.
6. **protocol-and-worker-supervision** is Git-renamed to
   `protocol-and-owner-lifecycle` everywhere. Its layer becomes
   `daemon-and-device-owner-lifecycle`. Its protocol ID becomes
   `turnvector.benchmark.owner-lifecycle.v1`. Its claim scopes become
   `local_client_protocol_conformance` and `device_owner_lifecycle_conformance`.
7. **certification-envelopes** retains its lane ID, four behavior obligations,
   five `record_state` values, and five gates, but replaces its historical
   applicability authority with the complete current exact identity set. It
   also replaces its wall-clock record-expiry state with evidence staleness
   because TurnVector Certification Records are immutable and have no invented
   wall-clock expiry.

## Fixture Provenance and Absorbing Taint Order

The owner-lifecycle lane preserves 24 CasePlans and five gates through
one-for-one semantic replacements:

- Matrix `daemon_outcome` values: `normal`, `failure_before_backend_initialization`,
  `failure_during_turn`, `safe_point_timeout`, `malformed_client_frame`,
  `duplicate_client_command`.
- Matrix `client_protocol_relation` values: `exact`, `compatible`, `incompatible`,
  `unknown_capability`.
- Cases: `owner-initialization`, `bounded-client-transport`, `mid-turn-daemon-failure`,
  `fresh-daemon-runtime`.
- Gate metrics: `simultaneous_device_owner_count<=1`;
  `backend_calls_before_initialization_count=0`;
  `successful_receipt_after_daemon_loss_count=0`;
  `client_transport_max_frame_bytes<=certification_record.client_transport_max_frame_bytes`;
  `client_transport_latency_p99_us<=certification_record.client_transport_latency_p99_us`.
- Artifacts: `manifest`, `environment`, `bootstrap_trace`, `client_transport_trace`,
  `process_trace`, `turn_receipts`, `report`, `checksums`.
- Fixture operations: `launch-fixture-daemon`, `inject-daemon-outcome`,
  `inspect-daemon-fail-closed`. External input becomes `daemon-build`.

The replacement fixture models one daemon process containing a fake Device
Executor and never claims a real Backend Interface. Fixture evidence is always
`not_claimable_fixture`. It does not launch or describe a separate MLX Worker.

Before the first CasePlan START, every selected lane publishes one LaneContext
`execution_provenance` value. The replacement owner-lifecycle implementation
uses `execution_provenance=benchmark_fixture` and
`fixture_id=owner-lifecycle-device-executor-v1`; `production_subject` and
`benchmark_fixture` are the only values, and missing, unknown, or
driver/context disagreement is fail-closed.

`LaneController` owns `run_fixture_taint` with states `clean` and
`fixture_tainted`. It starts `clean` and, while binding pre-dispatch LaneContext,
applies the absorbing transition `clean -> fixture_tainted` if any selected
driver, collector, fixture helper, or LaneContext has `benchmark_fixture`
provenance. No transition returns to `clean`. Fixture code cannot be selected
or invoked after the first case START; an attempted late selection is a contract
failure and also leaves the run `fixture_tainted`.

Claimability evaluates `run_fixture_taint` before subject kind, lane results,
repository evidence, source match, or authority readiness. `fixture_tainted`
always yields `not_claimable_fixture`, even for a non-fixture subject with every
lane/gate passing and exact source authority. RunManifest, global report, and
RunSeal bind the taint state and the sorted fixture IDs.

PR 3 adds this controller transition and its adversarial tests before PR 4 can
activate expectation v2; PR 1 does not activate it.

## Certification Matrix and State Rules

The successor certification-envelopes lane preserves its matrix shape and
generic fail-closed oracle while expanding from 55 to 95 CasePlans:

- `changed_identity` values are `none`, `model_revision`, `phase`, `batch`,
  `shape`, `execution_route_identity`, `adapter_build`, `mlx_build`,
  `backend_interface_revision`, `device_class`, `gpu_configuration`,
  `memory_size`, `os_version`, `turnvector_daemon_build`,
  `backend_bootstrap_manifest`, `backend_capability_descriptor`,
  `generation_semantics_identity`, `backend_resource_signal_contract`, and
  `backend_operation_bound_set` (19 values).
- `record_state` values are `applicable`, `missing`, `stale_evidence`,
  `quarantined`, and `superseded`. The old `expired` value is forbidden.
- Its suite external inputs are `turnvector-daemon-build`, `adapter-build`,
  `mlx-build`, and `backend-interface-descriptor`. The successor suite
  and fixture contain no `daemon-worker-build`, `worker_build`, or
  `protocol_capability` identity.
- `schemas/certification-record-v1.schema.json` is superseded in the successor
  contract by `schemas/certification-record-v2.schema.json` with schema
  `turnvector.benchmark.certification-record.v2`. The strict required fields are
  `schema_version`, `id`, `subject_build_identity`, `protocol_version`,
  `environment_identity`, `matrix_applicability`, and `thresholds`. `issued_at`
  and `expires_at` are forbidden. Exact file identity, environment identity,
  complete matrix applicability, pre-run threshold completeness, stale-evidence
  status, quarantine, and supersession remain fail-closed; controller wall time
  is provenance only and never record applicability authority.
- The certification case schema, expectation matrix, reference certification
  fixture, reference subject, loader, controller, README, BENCHMARK-DESIGN, and
  tests change together. `schemas/performance-certification-v1.schema.json` is a
  separate Benchmark peer-comparison evidence contract outside this TurnVector
  request-certification record and is not changed by D0.

The certification applicability matrix is 19 `changed_identity` values x 5
record states: `applicable`, `missing`, `stale_evidence`, `quarantined`, and
`superseded`. The successor schema forbids `issued_at`, `expires_at`, and the
old `expired` state. Controller wall time is provenance only and never
record-applicability authority.

## Artifact Causality and Exact Partition

The 95 required-artifact memberships remain unchanged, but they are partitioned
by role rather than all being treated as EvidenceBundle members.

| Artifact role | Count |
| --- | --- |
| Raw pre-judge memberships | 52 |
| Pre-dispatch context memberships | 21 |
| Post-gate outputs | 22 |

Raw pre-judge evidence excludes the four Benchmark controller artifact IDs
`manifest`, `environment`, `report`, and `checksums`. The 12 lane-local raw
membership counts are `3,1,4,4,5,4,5,5,6,7,4,4`, totaling R_DE=52 across E=41
distinct raw evidence-source IDs. Only these records may occur in
EvidenceBundleContract membership and R_HE.

Pre-dispatch lane context contains `manifest` for every lane and `environment`
for the nine lanes that require it. The lane-local counts are
`1,2,2,1,2,2,2,2,2,1,2,2`, totaling R_LC=21 across two context artifact types.
They are completely published before the first case START and are referenced by
every CaseResult and EvidenceBundle in that lane; they are not duplicated into
EvidenceBundle membership. D0 freezes causality only; D1 must separately qualify
any host-crash durability claimed for run artifacts.

Post-gate lane output contains `report` for every lane and `checksums` for the
ten lanes that require it. The lane-local counts are
`1,2,2,1,2,2,2,2,2,2,2,2`, totaling R_LP=22 across two output artifact types. A
lane report consumes its complete GateResults. A checksums artifact consumes
all prior lane context, raw evidence, judge/gate, and report bytes while
excluding its own path. Neither may be consumed by a judge or gate.

The compile-to-run causal prefix is `START(t)` -> `CoveragePlan` -> successful
receipt -> `TERMINAL(t)` -> finalized virtual-history digest/count ->
`RunEnvironment`. `CoveragePlan` binds only the through-`START(t)` prefix; the
receipt binds `CoveragePlan`; `TERMINAL` binds the receipt and output;
`finalize_history` then streams the closed events; and `RunEnvironment` is the
first persisted artifact allowed to bind the final history digest/count and the
successful receipt. The run artifact DAG is `RunEnvironment` -> lane context ->
`CaseResult` -> raw `EvidenceMember` -> `EvidenceBundle` -> `JudgeResult` ->
`GateInputSet` -> `GateResult` -> lane report -> lane checksums -> global
`RunManifest` -> global report -> `RunSeal`. Every artifact arrow binds
predecessor digests; no artifact names a later digest.

The current controller writes lane `manifest`/`environment` after
`_run_one_lane`. That observed ordering is not accepted real-run evidence. D0
compiles the corrected static contract and keeps fixture execution
nonclaimable; D1 must move these context writes before dispatch before any real
Core execution can satisfy the plan.

## Obligation Catalog Contract

### Canonical Contract Encoding

JSON contract files are strict UTF-8 without BOM, two-space indented, keys in
lexical order, LF line endings, and one final LF. Identity is SHA-256 over
exact file bytes.

JSONL catalog records are strict UTF-8, one compact JSON object per line,
lexical object keys, ASCII escaping, LF after every line including the last,
and records sorted by stable ID after the header.

Parsers reject duplicate object keys, unknown fields, floats, noncanonical
JSONL bytes, duplicate or unsorted identifiers, invalid UTF-8, BOM, absolute
paths, traversal, and symlinks.

Generated `CoveragePlan`, `ContractFailure`, receipt, and chronology records use
compact lexical-key JSON plus one LF. Integers are canonical nonnegative
decimal u64 values. Arrays retain schema-defined order.

### Catalog Contract

The catalog path is `authority/obligation-catalog-v1.jsonl`. Its schema family
is `turnvector.benchmark.obligation-catalog.v1`.

Header fields: `kind=catalog`, `schema_version`, `id`,
`profile_id=turnvector-implementation-v2`, `lineage_id`, `predecessor`,
`design_gate_revision`, `source_reconciliation_sha256`, `expectation_sha256`,
`compile_custody_policy_sha256`, `custody_domain_id`, `custody_domain_sha256`,
`compile_custody_lineage_id`, `t_max`, `required_obligation_count=46`,
`record_count`.

`compile_custody_lineage_id` is the predeclared stable string
`tvb-qualification-d0-catalog-v1`. `t_max` is 8. Byte-identical accepted
authority cannot receive a new lineage ID.

Before the catalog-content gate, CompileCustody initializes exactly one
host-bound external CustodyDomain. The later accepted catalog binds that
domain's exact ID and digest. Changing its canonical root identity changes
authority and requires a separately gated successor catalog naming the
preserved predecessor chronology; it is never an attempt reset.

Exactly 46 required obligations exist, one for every successor expectation
behavior-case ID qualified by lane ID. A later catalog-content gate freezes
their IDs, exact source byte ranges, source digests, wording, gate ownership,
and statuses. There are 58 gates, and the obligation/gate relation is the
explicit lane-local many-to-many relation: every required obligation and every
gate has at least one incident pair, and every expanded CasePlan in a lane
obtains one enforcement path for every accepted obligation-gate pair in that
lane.

Obligation fields: `kind=obligation`, `id`, `required`, `claim_class`,
`source_path`, `source_file_sha256`, `section_start`, `section_end`,
`section_sha256`, `module_ids`, `seam_id`, `observable_seam`, `evidence_grade`,
`invalidation_rule`, `lane_id`, `behavior_case_id`, `readiness_status`,
`blocker_ids`, `design_gate_revision`.

`readiness_status` is `design_ready`, `adapter_blocked`, or
`environment_blocked` for required records. `intentionally_out_of_scope` is
allowed only when `required=false` and therefore is outside O_p.

Required records cannot be omitted because they are adapter-blocked. They
compile, but ClaimPass later requires the observable production Seam to be
available.

The catalog-content gate must review all complete authority sources and every
range. CoverageCompiler proves catalog-relative closure, not semantic
completeness of prose.

## Traceability Ledger Contract

The ledger path is `authority/traceability-v1.json`. Its schema is
`turnvector.benchmark.traceability.v1`.

It binds exact source-reconciliation, expectation, catalog, custody-policy,
custody-domain, lane-suite, case-schema, judge-contract,
evidence-bundle-contract, raw evidence-source, lane-context-contract,
post-gate-output-contract, gate, and negative-test digests.

It contains an explicit lane-local many-to-many obligation-gate relation. Every
required obligation and every gate has at least one incident pair. Every
expanded CasePlan in a lane obtains one enforcement path for every accepted
obligation-gate pair in that lane, using the obligation's independent judge,
the lane EvidenceBundleContract, and the gate.

There are exactly 46 judge contracts, one per required obligation; 12
EvidenceBundleContracts, one per lane; 41 distinct raw evidence-source IDs
after one-for-one owner-lifecycle artifact renames; 21 lane-context memberships
across two context types; 22 post-gate-output memberships across two output
types; 46 judge-semantic negative-test templates; 58 aggregate gate-semantic
templates; and 58 gate-plumbing templates.

Each enforcement path maps to exactly one judge-semantic negative template.
Each gate maps to exactly one aggregate-semantic template and exactly one
plumbing template.

The ledger uses canonical explicit entity lists and lane-local gate-ownership
records. The compiler expands matrices deterministically; it does not infer
obligations or gate ownership from prose.

## Authority Compile Contract - Durable Normative Transcription

The compiler and custody wrapper contracts transcribed below bind every successor
implementation. Identifiers are verbatim; nothing is omitted or renamed, and no
limits or execution-closure details are added beyond the source contract.

### CoverageCompiler interface

```
compile(CompilePermit, AuthoritySnapshot, ObligationCatalog, BenchmarkExpectation, TraceabilityLedger, CompileLimits) -> CoveragePlan | ContractFailure
```

The interface is private to `turnvector_benchmark.authority`. The CLI can invoke
only the custody wrapper, never the bare compiler.

### AuthoritySnapshot and repository-control descriptor duties

`AuthoritySnapshot` binds repository identity, exact HEAD, clean status,
normalized current source paths, complete regular-file digests, each nonempty
half-open section range/digest, and a repository-control descriptor.

Before any Git process, the descriptor binds the worktree `.git` indirection
bytes, resolved git-dir/common-dir physical identities,
`index`/`HEAD`/`config`/`config.worktree`/`info-exclude`/`shallow` bytes or
explicit absence, plus every bounded worktree `.gitignore` path/content;
rejects grafts, alternates, replacement refs, gitlinks,
assume-unchanged/skip-worktree index flags, active control lock files, every
common-dir/git-dir `info/attributes` entry, every worktree entry named
`.gitattributes`, every `include`/`includeIf` directive, and every non-admitted
config key; and records the exact controlled Git command results.

Historical reconciliation objects are verified with the qualified absolute Git
binary using `cat-file` at their historical revision. Snapshot source files are
opened no-follow, hashed, and retained as bounded descriptors; the same paths,
bytes, repository-control descriptor, HEAD, and clean status are revalidated
before a successful compiler output may be published.

The compiler validates strict schemas, canonical order, source identity, all
entities, all relations, exact joins, degree caps, negative-test coverage, and
resource caps before success.

### CoveragePlan and ContractFailure identity/output semantics

`CoveragePlan` fields include `schema_version`, `profile_id`,
`custody_domain_id`, `custody_domain_sha256`, `custody_lineage_id`, `attempt`,
`t_max`, `start_event_sha256`, `chronology_prefix_sha256`,
`chronology_prefix_byte_count`, `compiler_build_sha256`, all exact input
digests, source revision/status/file/range digests, every entity-set digest,
every relation-set digest, exact counts, exact limits, expected compile/run key
names, and limitations. `chronology_prefix_sha256` and
`chronology_prefix_byte_count` identify exactly the virtual chronology prefix
through this attempt's START; neither `CoveragePlan` nor `ContractFailure`
contains a final compile-history digest or count.

`ContractFailure` has the same identity prefix, `outcome=contract_failure`,
exactly one primary error variant, at most 64 diagnostics of at most 1024 UTF-8
bytes each,
`discarded_diagnostic_count`, observed counts/bytes through failure, and no
plan digest.

### Bounded wrapper publication role

The compiler returns bounded canonical output bytes only to `CompileCustody`.
The wrapper alone performs exclusive staging, complete write, permanent barrier,
digest, and atomic attempt-object rename; a partial output is never accepted.

### Exact ContractFailure variants

The compiler returns exactly these `ContractFailure` variants, in this fixed
order, with no omission or renaming:

`authority_invalid_canonical_json`
`authority_unknown_field`
`authority_duplicate_key`
`authority_invalid_identifier`
`authority_duplicate_identifier`
`authority_order_violation`
`authority_absolute_path`
`authority_path_traversal`
`authority_symlink`
`authority_non_regular_file`
`authority_missing_source`
`authority_missing_historical_object`
`authority_repository_mismatch`
`authority_revision_mismatch`
`authority_dirty_repository`
`authority_file_digest_mismatch`
`authority_invalid_section_range`
`authority_section_digest_mismatch`
`authority_repository_control_unsupported`
`catalog_digest_mismatch`
`catalog_gate_revision_mismatch`
`catalog_lineage_mismatch`
`catalog_predecessor_mismatch`
`catalog_record_limit_exceeded`
`catalog_orphan_source`
`catalog_invalid_status`
`expectation_digest_mismatch`
`traceability_digest_mismatch`
`traceability_unknown_entity`
`traceability_duplicate_entity`
`traceability_orphan_obligation`
`traceability_orphan_case`
`traceability_orphan_judge`
`traceability_orphan_bundle`
`traceability_orphan_gate`
`traceability_orphan_evidence`
`traceability_relation_mismatch`
`traceability_degree_cap_exceeded`
`traceability_empty_required_set`
`traceability_negative_test_coverage_missing`
`resource_checked_arithmetic_overflow`
`resource_platform_size_overflow`
`resource_source_cap_exceeded`
`resource_section_cap_exceeded`
`resource_input_cap_exceeded`
`resource_index_cap_exceeded`
`resource_output_cap_exceeded`
`resource_allocation_failed`

### CompileCustody-only rejection variants and pre-request refusal codes

The CompileCustody-only rejection variants are `custody_domain_mismatch`,
`custody_registry_mismatch`, `custody_lineage_binding_mismatch`,
`custody_namespace_exists`, `custody_identity_changed`,
`custody_attempt_limit_exceeded`, `custody_invalid_attempt_sequence`,
`custody_compile_permit_reuse`, `custody_compiler_identity_mismatch`,
`custody_input_identity_mismatch`, `custody_atomic_attempt_object_invalid`,
`custody_output_missing`, `custody_output_digest_mismatch`,
`custody_receipt_missing`, `custody_receipt_digest_mismatch`,
`custody_chronology_invalid`, `custody_post_success_attempt`,
`custody_incomplete_history`, and `custody_interrupted_attempt`. The last
variant is the complete wrapper-generated `ContractFailure` for a recoverable
dangling START.

The separate pre-request refusal codes `custody_execution_closure_mismatch`,
`custody_repository_control_unsupported`, `custody_storage_profile_mismatch`,
and `custody_storage_barrier_unavailable` can be returned only by isolated
bootstrap/initialization/open before an attempt or invalid request is accepted;
they never appear as a `ContractFailure`, receipt, or event.

An execution-closure traversal cap/global-arena failure maps to
`custody_execution_closure_mismatch`, and a repository-control traversal
cap/global-arena failure maps to `custody_repository_control_unsupported`. The
compiler-side `authority_repository_control_unsupported` variant exists only
for a descriptor inconsistency discovered after a qualified preflight and
START; a facility rejected during preflight returns the custody refusal without
START.

### Fail-stop/recovery distinction after START

A runtime/tool/environment/repository-control closure drift, revalidation
resource failure, or barrier failure after START is not processed under an
unqualified runtime and is not fabricated as a durably recorded rejection: the
process fail-stops without returning a permit or rejection, and a later process
matching the frozen closure and storage profile recovers the dangling START as
`custody_interrupted_attempt`.

Once a START exists and the execution/storage closure remains qualified, a safe
valid chain preserves a wrapper failure as that attempt's
`ContractFailure`/receipt/`TERMINAL`. Unsafe execution closure, repository
control, domain, storage profile, registry, chain, symlink, or immutable-object
corruption fails closed without pretending that an audit publication was safe.

### VIOLATION reasons and durability/invalidating semantics

The VIOLATION reason values are `duplicate_begin`, `duplicate_terminal`,
`permit_reuse`, `attempt_limit_exceeded`, `post_success_attempt`,
`compiler_identity_mismatch`, `input_identity_mismatch`, and
`alternate_entrypoint_request`. With a valid custody domain and chain, the
wrapper atomically publishes and permanently flushes one immutable VIOLATION
event object before returning rejection for any such request. Any VIOLATION
makes `CompileChronologyValid` false, including a VIOLATION published after an
earlier success; the invalid request cannot disappear behind that success.

## Compile Limits

- Integers are positive checked u64 unless zero is explicitly permitted.
- authority_file_count_max=256 files.
- authority_file_bytes_max=4,194,304 bytes per file.
- authority_total_bytes_max=67,108,864 bytes.
- authority_section_count_max=1024 ranges.
- authority_section_bytes_total_max=33,554,432 hash-update bytes, counted with overlap multiplicity.
- serialized_input_bytes_total_max=16,777,216 bytes.
- catalog_record_count_max=512 records.
- case_plan_count_max=4096.
- obligation_count_max=256.
- judge_count_max=256.
- evidence_bundle_count_max=64.
- evidence_source_count_max=256.
- gate_count_max=256.
- each negative-test class count_max=256.
- path_count_max=32,768.
- paths_per_obligation_max d_H=1024.
- evidence_members_per_bundle_or_path_max d_DE=16.
- context_artifacts_per_lane_max d_LC=2.
- post_gate_outputs_per_lane_max d_LP=2.
- paths_per_case_max d_CH=32.
- paths_per_gate_max d_GH=512.
- judge_negative_tests_per_path_max d_HNJ=1.
- aggregate_negative_tests_per_gate_max d_GNG=1.
- plumbing_negative_tests_per_gate_max d_GNP=1.
- authority_hash_buffer h_max=65,536 bytes.
- largest single serialized parser input l_max=4,194,304 bytes.
- logical retained index arena x_max=33,554,432 bytes.
- output streaming chunk q_max=65,536 bytes.
- CoveragePlan or ContractFailure p_max=33,554,432 bytes.
- per receipt max=65,536 bytes.
- custody domain record max=32,768 bytes.
- custody registry genesis max=32,768 bytes.
- custody registry binding event max=16,384 bytes.
- compile chronology genesis max=32,768 bytes.
- compile chronology event max=16,384 bytes.
- one in-progress registry or chronology event staging file max=16,384 bytes; this is temporary
  atomic-publication headroom, never a committed event or attempt.
- one in-progress attempt-object staging directory max=33,619,968 logical payload bytes; it is
  atomically renamed into either a final attempt object or an interruption quarantine and is never
  retained as an additional copy.
- interrupted staging quarantine max=33,619,968 bytes per attempt, equal to p_max plus one
  receipt_max.
- t_max=8 attempts.
- execution_closure_record_count_max=4,096 filesystem records.
- execution_closure_file_bytes_max=83,886,080 bytes (80 MiB), including the authority source
  closure.
- execution_closure_path_bytes_max=4,096 UTF-8 bytes.
- execution_closure_path_bytes_total_max=16,777,216 bytes, the checked product of record and
  per-path maxima.
- execution_closure_symlink_target_bytes_max=4,096 UTF-8 bytes and
  execution_closure_symlink_target_bytes_total_max=16,777,216 bytes.
- execution_closure_loaded_image_count_max=512.
- execution_closure_loaded_image_path_bytes_max=4,096 UTF-8 bytes and
  execution_closure_loaded_image_path_bytes_total_max=2,097,152 bytes.
- canonical_directory_entry_count_max=4,096 immediate entries for every execution-closure or
  repository-control directory.
- canonical_directory_name_bytes_max=4,194,304 total strict UTF-8 immediate-name bytes retained for
  one directory sort.
- canonical_directory_sort_index_bytes_max=65,536 bytes for two 4,096-entry arrays of checked u64
  logical indices.
- repository_control_entry_count_max=32,768 combined no-follow worktree and git-dir/common-dir
  control-namespace entries.
- repository_control_path_bytes_max=4,096 UTF-8 bytes per relative path.
- repository_control_path_bytes_total_max=134,217,728 bytes, the checked product of the two
  preceding maxima.
- repository_control_file_bytes_max=4,194,304 bytes per .git pointer, index, HEAD, config,
  config.worktree, info/exclude, shallow, ref, or other admitted control regular file;
  repository_control_file_bytes_total_max=33,554,432 bytes across all admitted control and ignore
  files.
- repository_control_config_bytes_total_max=1,048,576 bytes across common and worktree config.
- repository_control_ignore_file_count_max=256 regular .gitignore files.
- repository_control_ignore_file_bytes_max=1,048,576 bytes per .gitignore file.
- repository_control_ignore_bytes_total_max=4,194,304 bytes.
- repository_control_git_path_record_count_max=32,768 records per fixed path-enumeration command.
- repository_control_git_output_bytes_max=134,217,728 bytes per fixed path-enumeration command and
  4,194,304 bytes per other fixed command.
- repository_control_git_stderr_bytes_max=1,048,576 bytes per command.
- repository_control_git_timeout_seconds=60 per command.
- authority_child_stdout_bytes_max=33,652,736 bytes (p_max plus receipt_max plus a 32,768-byte
  transport envelope) and authority_child_stderr_bytes_max=1,048,576 bytes.
- authority_child_timeout_seconds=600 per parent invocation.

The logical arena cap excludes the Python interpreter, stack, imported module pages, and allocator
metadata. Those are recorded as compiler environment facts and may receive an OS-level execution
cap in a later operational hardening design. Every retained compiler-owned payload, normalized
identifier, entity, relation endpoint, and active section-hash state is charged to x_max before
retention. This is a logical auxiliary-memory bound, not an RSS claim.

## Qualified Authority Execution Closure

- The only real authority/custody path is an exact child invocation of
  /Applications/Xcode-26.5.0.app/Contents/Developer/usr/bin/python3 with argv -I -S -B plus the
  verified absolute authority_runtime bootstrap path and cwd=/var/empty. subprocess uses
  shell=false, stdin/stdout bounded pipes, close_fds=true except the declared pipes,
  stdout<=33,652,736 bytes, stderr<=1,048,576 bytes, timeout=600 seconds, and the exact 12-key
  environment frozen under `Frozen Qualified Profile Values`. The stdout cap is exactly
  p_max+receipt_max+32,768
  transport-envelope bytes; canonical plan/receipt custody remains bounded separately and the
  envelope has no authoritative role. The execution closure binds /var/empty's no-follow
  realpath/device/inode/uid/gid/mode and revalidates it before START/publication. No caller current
  working directory, user site, site module, PYTHON*, DYLD*, DEVELOPER_DIR, XDG*, SSH*, locale,
  shell, or inherited Git state enters the child. A parent timeout/overflow kills and reaps the
  child and returns only an unbound transport error; if START had become durable, the next
  qualified child must recover it, so transport failure cannot fabricate or erase an attempt.
- The child verifies sys.flags.isolated=1, no_site=1, no_user_site=1, dont_write_bytecode=true, the
  exact implementation/version/build, launcher symlink identity, base executable, and initial
  three-entry sys.path under the frozen Python framework. It verifies its bootstrap digest before
  adding the exact Benchmark source root; thereafter an import guard admits only built-in/frozen
  modules, files in the verified authority source closure, files in the verified Python framework
  tree, or native images in the exact dyld shared cache. Any other origin fails before custody
  access.
- Canonical tree identity recursively enumerates without following symlinks. For each directory it
  materializes only that directory's immediate strict UTF-8 name bytes, rejects more than 4,096
  entries or 4,194,304 retained name bytes, charges those bytes, two at-most-4,096-entry
  checked-u64 logical index arrays (65,536 bytes), and normalized records to x_max, and sorts
  lexical bytes with a private iterative stable bottom-up merge sort before descending. Its merge
  operation compares each pair once and therefore performs at most n_d*ceil(log2(max(2,n_d))) name
  comparisons. It then hashes one compact lexical-key JSON+LF record per directory, regular file,
  and symlink. Relative POSIX paths and symlink targets are each at most 4,096 bytes;
  execution-closure total relative-path bytes and total symlink-target bytes are each at most
  16,777,216, so U_exec<=33,554,432. Directory records bind path/mode; file records bind
  path/mode/size/content SHA-256 read O_NOFOLLOW with pre/post fstat; symlink records bind
  path/mode/target. Duplicate/traversing/invalid names or targets, lstat/fstat races, cap overflow,
  and other file types fail closed. Exact root realpath/device/inode/uid/mode, record/file/symlink
  counts, total path and symlink-target bytes, content-byte total, per-directory sort counters, and
  tree digest are all bound.
- The execution closure binds the whole Xcode Python 3.9 framework tree, exact launcher, actual
  Xcode Git binary and complete Git exec tree, df/diskutil/sw_vers/csrutil binaries, macOS/Darwin
  identity, dyld shared-cache UUID, authenticated-root/SIP observations, exact
  argv/environment/fixed-cwd identity, and tree algorithm. Every loaded native image before START
  and publication must be either in the verified Python tree or reported in the exact shared cache;
  any other image fails closed. The image set uses the same private merge sort over at most
  N_img=512 strict paths, each at most 4,096 bytes and U_img<=2,097,152 total bytes, with two
  logical index arrays <=8,192 bytes. Filesystem record count N_exec max is 4,096, total
  runtime/tool plus source regular-file bytes max W=83,886,080, and per-filesystem-path UTF-8 bytes
  max 4,096. The observed runtime/tool seed is 2,206 filesystem records and 70,664,091 bytes before
  source files and the final cwd identity are added.
- The source closure is a policy-listed exact set containing authority_runtime bootstrap and every
  authority/compiler/custody module. It excludes the policy data file to avoid a cycle.
  source_closure_sha256 is the canonical tree digest of that exact set. execution_closure_sha256 is
  SHA-256 of canonical fields for runtime/tool/system/argv/environment/fixed-cwd identities only.
  compiler_build_sha256 is SHA-256 of compact lexical-key JSON+LF containing schema_version,
  source_closure_sha256, and execution_closure_sha256. The policy binds all three values;
  START/output/receipt/TERMINAL bind policy, execution closure, and compiler build. Any source or
  execution-closure change therefore changes compiler_build and requires a new policy, catalog
  gate, and lineage rather than reusing q.

- Git is invoked only as /Applications/Xcode-26.5.0.app/Contents/Developer/usr/bin/git with the
  fixed environment, fixed GIT_EXEC_PATH, GIT_NO_REPLACE_OBJECTS=1, GIT_ATTR_NOSYSTEM=1,
  system/global config paths fixed to /dev/null, no optional locks or prompts, and fixed command
  templates. Before that first invocation, the child manually parses the bound local config bytes
  and rejects include/includeIf, continuation syntax, duplicate normalized keys, unknown grammar,
  invalid value domains, and every key outside three closed sets. Semantic keys are
  core.repositoryformatversion=0, core.bare=false, bound booleans
  core.filemode/core.ignorecase/core.precomposeunicode/core.logallrefupdates, and
  extensions.worktreeConfig as absent/false or true with a separately bound and strictly parsed
  git-dir config.worktree. Git-inert metadata keys are user.name/user.email/user.signingkey,
  gpg.format/gpg.program, commit.gpgsign, tag.gpgsign, core.sshCommand,
  remote.<name>.url/pushurl/fetch, and branch.<name>.remote/merge. The one application-only
  admitted key is codex.localEnvironmentConfigPath, which Git ignores and whose bytes remain bound.
  Metadata-only keys are unreachable because the command grammar contains no transport, mutation,
  signing, commit, tag, config, or application operation. Every alias.*, filter.*,
  diff.*.(command|textconv), merge.*.driver, credential.*, core.attributesFile, core.excludesFile,
  core.fsmonitor, core.hooksPath, submodule.*, protocol.*, url.*, http.*, ssh.*, pager.*,
  interactive.*, sequence.*, and other unknown key is rejected. shell, aliases, network,
  credentials, hooks, filters, external diff, and command-reachable config-selected executables are
  therefore unreachable. Commands use explicit --git-dir/--work-tree after manual .git indirection
  validation, stdout caps above, stderr<=1,048,576 bytes, strict UTF-8 or explicitly binary object
  output, stdin=/dev/null, close_fds=true, and timeout=60 seconds. A timeout/overflow before START
  returns custody_repository_control_unsupported; after START it fail-stops for qualified
  interrupted-attempt recovery.

- Repository-control validation binds the .git pointer, resolved git-dir/common-dir identities and
  timestamps, index, HEAD, strictly parsed common and worktree config, info/exclude, and shallow
  bytes or explicit absence. Every present control file must be a no-follow regular file read with
  lstat/open(O_NOFOLLOW)/fstat identity agreement before and after bounded hashing; each required
  absence is established by no-follow lstat of the bound parent/name and is checked again after
  Git. Deterministic no-follow absence checks cover index.lock, HEAD.lock, config.lock,
  packed-refs.lock, shallow.lock, and the bounded recursively enumerated refs namespace's *.lock
  entries. objects/info/alternates, info/grafts, replace refs, common-dir info/attributes, git-dir
  info/attributes, and gitlinks are forbidden. Before Git, a bounded no-follow walk of the physical
  worktree applies ASCII case-folding to every component, rejects any component colliding with
  .gitattributes, rejects non-lowercase spellings colliding with .gitignore, and requires each
  exact .gitignore entry to be a no-follow regular file whose path, mode, size, and content digest
  are bound. At most 256 ignore files, 1,048,576 bytes each, and 4,194,304 total ignore bytes are
  admitted. Every worktree, refs, git-dir, or common-dir traversal uses the same private
  per-directory bottom-up merge sort and 4,096-entry/4,194,304-name-byte/65,536-index-byte bounds
  as execution closure. The combined worktree and git-dir/common-dir control scans visit at most
  32,768 entries, reject paths above 4,096 UTF-8 bytes or total relative-path bytes above
  134,217,728, reject duplicate folded control-name keys, never enter unrelated object payloads,
  and reject traversal races. They stream canonical stability digests over each
  path/type/device/inode/mode/size/nanosecond-mtime/nanosecond-ctime; the same walks, per-directory
  sort counters, ignore-content set, and digests must match after Git and again before publication,
  so a content change alters its entry tuple/digest and even a transient add/remove alters its
  parent-directory tuple inside the stated nonhostile local filesystem boundary. After config and
  attribute-source absence are proven, fixed ls-files --stage -z, ls-files -v -z, ls-files -t -z,
  and ls-tree -rz HEAD commands each admit at most 32,768 path records, independently apply the
  same component rule, reject every attributes collision or noncanonical ignore collision, reject
  any 160000 mode, reject every lowercase -v tag indicating assume-unchanged, and reject every S -t
  tag indicating skip-worktree. Status then uses porcelain v1 -z, --untracked-files=all,
  --ignore-submodules=none, and final fixed -c overrides for core.fsmonitor=false,
  core.untrackedCache=false, core.attributesFile=/dev/null, core.excludesFile=/dev/null,
  core.fileMode, core.quotepath=false, diff.external=, and diff.trustExitCode=false. The descriptor
  includes the strict-parser version, control-name-key rule, every absence proof, stability
  digests, exact .gitignore set, index-flag proofs, scan counts/path-byte totals, admitted
  normalized config tuples plus raw config digests, exact command argv/results, source
  descriptors/digests, HEAD, historical blobs, and clean status; all are recomputed before
  successful publication. Any local configuration, index flag, lock, ignore source,
  attributes-source drift, or directory-sort cap overflow either changes the descriptor or fails
  closed before it can affect Git.

- The bootstrap verifies the entire execution closure and the pre-Git repository-control
  absence/config proof before initialize_domain/open_lineage and immediately before START. While
  holding the custody lock it recomputes the entire closure and repository/source descriptor after
  compilation but before publishing the attempt pair. An execution mismatch before START returns
  only custody_execution_closure_mismatch; an unsupported repository control before START returns
  only custody_repository_control_unsupported. Either mismatch after START fail-stops without
  caller result, leaving the dangling START for recovery by a later process that matches the same
  frozen closure and repository-control contract. Restoration cannot erase the consumed interrupted
  attempt. Synthetic tests mutate each identity dimension independently.

## Frozen Qualified Profile Values

These values are the accepted execution profile for the qualified authority path. They are frozen
now, before the catalog-content gate, and are recorded exactly as accepted evidence.

### Execution Provenance

| Kind | sha256 |
| --- | --- |
| Execution closure evidence | `0e72244f692fab210dd0752a619ea8972dd532dfa0fbc1003c7e2c93665a0d6c` |
| Execution probe source | `2a2f207c30d6e1dc111fd46d1f2500edcfa92ed88994b4c0c1d1d7e00618f7a0` |
| Observation seed `execution_closure_sha256` | `e350d1f9644a059720fceb0bafc7ca2457ee7ce2545d6990b45b1ff0a88cf2e4` |

The provenance digests identify accepted evidence only and do not make temporary probe paths runtime
inputs. The observation seed is not the final `compiler_build_sha256`; the final `source_closure_sha256`
and `compiler_build_sha256` are mechanically frozen only after PR7 source exists and require the later
catalog-content gate.

### Host/System

| Field | Value |
| --- | --- |
| host | `Lay2-Studio.local` |
| kernel | Darwin 25.4.0 arm64 |
| product | macOS 26.4.1 build 25E253 |
| dyld shared cache UUID | `2d40543a-792e-37b8-978d-3d7030e1aa81` |
| Authenticated Root | enabled |
| System Integrity Protection | enabled |
| root mount | APFS, sealed, local, read-only, journaled |

### Python

| Field | Value |
| --- | --- |
| launcher | `/Applications/Xcode-26.5.0.app/Contents/Developer/usr/bin/python3` |
| symlink target | `../../Library/Frameworks/Python3.framework/Versions/3.9/bin/python3` |
| launcher device/inode/uid/mode | 16777229 / 8220328 / 502 / 0755 |
| resolved executable | `/Applications/Xcode-26.5.0.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9` |
| resolved executable sha256 | `569155c6fb5480906280dc36764eb441a2be437c4df4292e9d613e55e2cd72fc` |
| CPython | 3.9.6 Clang 21.0.0 |
| flags | `-I -S -B` |
| framework root | `/Applications/Xcode-26.5.0.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9` |
| framework root device/inode/uid/mode | 16777229 / 8003954 / 502 / 0755 |
| tree sha256 | `e0abc1845e65c3d7a075981fc60610086ed4be100abec17754aef48e57932d77` |
| tree counts/bytes | records=2004; directories=184; files=1810; symlinks=10; file_bytes=48042075 |
| framework binary sha256 | `9484a605ade14461cf26087980a5a188ba0e24bbad2f91d227dac766427a12a6` |

The isolated `sys.path` is exactly `python39.zip`, `lib/python3.9`, `lib/python3.9/lib-dynload` under
the exact framework root.

### Git/Tools

| Field | Value |
| --- | --- |
| Git path | `/Applications/Xcode-26.5.0.app/Contents/Developer/usr/bin/git` |
| Git sha256 | `d70198cffcca6856af711447b09ac05b32666e1dfe066b4f5613fe8369abdb29` |
| Git device/inode/uid/mode | 16777229 / 8188881 / 502 / 0755 |
| Git version | `git version 2.50.1 (Apple Git-155)` |
| exec root | `/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core` |
| exec root device/inode/uid/mode | 16777229 / 8027035 / 502 / 0755 |
| exec tree sha256 | `0645ae9ca5b1bed3c37d8b5ac0529a52751310c01667ab33250bb489e895cb74` |
| exec tree counts/bytes | records=196; directories=1; files=49; symlinks=146; file_bytes=16319360 |

| Tool | sha256 |
| --- | --- |
| `/bin/df` | `6396e22533de5f3c818377b323b7bcee59dabe7d3811c5bbff01a38c0989a569` |
| `/usr/sbin/diskutil` | `520a71983659450859c6bb9275ba1b07690eef8a0b156aeaabe4a21656f8c0f3` |
| `/usr/bin/sw_vers` | `19944ce73b498bbc997043b8530984aff4c6ca9741cb3a044c9320369bb019dd` |
| `/usr/bin/csrutil` | `1e6ccb28c206373fc80ef1e79ee64fbd7ddb4c9183e9e46835d3ef965d2bce6e` |

### Sanitized Child Environment

`canonical_environment_sha256` is `a385433f0a999a343b1ada821e32cdc438e6dc04b257b1903e36ede2224d6026` and
the exact environment is:

```
GIT_ATTR_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
GIT_EXEC_PATH=/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core
GIT_NO_REPLACE_OBJECTS=1
GIT_OPTIONAL_LOCKS=0
GIT_TERMINAL_PROMPT=0
HOME=/var/empty
LANG=C
LC_ALL=C
PATH=/Applications/Xcode-26.5.0.app/Contents/Developer/usr/libexec/git-core:/usr/bin:/bin
TZ=UTC
```

All other variables, including `DEVELOPER_DIR`, `DYLD_*`, `GIT_*`, `LC_*`, `PYTHON*`, `SSH_*`, and
`XDG_*`, are absent.

### Storage Barrier Profile

Provenance: storage evidence SHA256 89226df70eda20fa10cfda163af1dbc147eefded54f8b25967c02db7f6a63337 and
probe-source SHA256 54e7b438d5b587e3d037321330489fb07e1ba89a1a11f1fa2cf63b403e616296. These identify
accepted evidence; temporary paths are not runtime inputs.

| Field | Value |
| --- | --- |
| storage_barrier_profile | darwin-apfs-internal-fullfsync-v1 |
| product | macOS 26.4.1 build 25E253 |
| kernel | Darwin 25.4.0 arm64 |
| device | /dev/disk3s5 |
| mount | /System/Volumes/Data |
| filesystem | APFS, local, journaled, writable |
| volume name | Data |
| VolumeUUID | F5888E67-5CC4-4700-A629-52442F8284E1 |
| BusProtocol | Apple Fabric |
| Internal | true |
| SolidState | true |
| Removable | false |
| Ejectable | false |
| MediaReadOnly | false |
| VolumeReadOnly | false |
| fcntl.F_FULLFSYNC | 51 |

Only these source-reproducible results are admitted: O_WRONLY regular-file F_FULLFSYNC result=0
errno=0; O_RDONLY|O_DIRECTORY parent-directory F_FULLFSYNC result=0 errno=0. Evidence lines 35-36
reporting read-only regular-file/reopen results are not reproduced by the manifested source and are
excluded; no code, test, proof, or claim may consume them. Ordinary fsync, F_BARRIERFSYNC, sync,
sleep, timing, O_RDONLY regular-file F_FULLFSYNC, and every weaker fallback are not authority.

## CompileCustody Implementation

- Modules: `turnvector_benchmark.authority_runtime` is the dedicated bootstrap and
  `turnvector_benchmark.compile_custody` owns custody. No new dependency; the qualification path
  uses only the frozen Xcode CPython 3.9 standard library closure. The ordinary CLI/controller
  process has no permit, compiler, Git-authority, barrier, or custody API; it can only launch the
  child and relay bounded request/result bytes.
- Policy path: `profiles/compile-custody-v1.json`. It binds all limits, catalog/profile IDs, stable
  lineage ID `q`, `t_max`, the canonical source-closure manifest/digest,
  `execution_closure_sha256`, `compiler_build_sha256`, isolated launcher argv, exact environment
  and fixed-cwd identity, child/Git timeout and stdout/stderr caps, tree algorithm/caps,
  native-image rule, controlled Git command templates and output caps, strict config parser/version
  and closed admitted-key grammar, repository-control walk/absence rules and caps, `CustodyDomain`
  schema, registry grammar, event grammar, allowed output schemas,
  `storage_barrier_profile=darwin-apfs-internal-fullfsync-v1`, exact `/bin/df`,
  `/usr/sbin/diskutil`, `/usr/bin/sw_vers`, and `/usr/bin/csrutil` executable digests, their parsed
  stable-field schemas, and `fcntl.F_FULLFSYNC=51`. It does not contain its own digest or the later
  catalog/domain digest, avoiding a cycle.
- The supported storage profile requires Darwin, frozen macOS 26.4.1 build 25E253, one
  writable APFS device mount, `Internal=true`, `SolidState=true`, `BusProtocol=Apple Fabric`,
  `Removable=false`, `Ejectable=false`, stable device/mount/VolumeUUID identity, and successful
  source-reproducible F_FULLFSYNC on an O_WRONLY regular probe file plus its O_RDONLY|O_DIRECTORY
  parent descriptor. `/usr/bin/sw_vers` supplies exact product/build; `/bin/df -P` resolves the
  canonical root to one device and mount; `/usr/sbin/diskutil info -plist` plus standard-library
  plistlib supplies only the whitelisted stable fields. The storage evidence identified above has
  lines 35-36 claiming read-only reopen results absent from the manifested probe source; those
  lines are excluded from the profile, and no code, proof, test, or claim may consume them. Missing
  tools, digest drift, extra/ambiguous records, parse/type failure, a changed stable field,
  non-APFS/network/external/virtual storage, or failed admitted file/directory probe is
  unsupported. There is no regular-file F_FULLFSYNC through O_RDONLY, `fsync`, F_BARRIERFSYNC,
  `sync`, sleep, or timing fallback.
- `permanent_barrier(fd)` retries `fcntl(fd,F_FULLFSYNC,0)` only on EINTR and succeeds only on
  zero. Any other errno is a barrier failure. For every object publication, the writer permanently
  barriers every complete file, permanently barriers its staging directory after entry creation,
  performs the same-filesystem atomic rename, then permanently barriers the destination parent
  directory. No caller-visible success, CompilePermit, compiler invocation, rejection, or later
  authoritative mutation occurs before the final parent-directory permanent barrier returns zero.
- `initialize_domain(control_root, policy)` is a one-time pre-catalog operation. It requires an
  existing empty no-follow external directory, takes an exclusive flock, verifies tool digests and
  stable storage facts, and runs the admitted
  create/write/O_WRONLY-file-FULLFSYNC/rename/O_RDONLY-directory-FULLFSYNC/remove/directory-FULLFSYNC
  probe before genesis. It creates one reserved profile-probe directory and then writes
  `custody-domain-v1.json` plus registry genesis with O_CREAT|O_EXCL and the permanent-barrier
  protocol. Registry genesis binds `domain_id`, root identity, reserved probe-directory identity,
  policy digest, and storage profile identity but not the later domain-record digest; the canonical
  domain record then binds `domain_id`, canonical realpath bytes, root and probe-directory
  device/inode/uid/mode, policy digest, host/OS identity, stable storage facts, tool digests,
  admitted probe result, and registry-genesis digest. The catalog-content gate later freezes this
  exact domain record digest, so the chain is acyclic.
- No compile API accepts a root path. `open_lineage(catalog, domain_record)` is callable only
  inside the qualified authority child. It first revalidates the complete execution closure, then
  requires the catalog-bound domain ID/digest, resolves the canonical root from that record, and
  revalidates root/probe-directory realpath/device/inode/uid/mode no-follow plus exact
  OS/tool/storage stable facts. It opens the preexisting domain lock, takes the exclusive flock,
  and validates the registry genesis and event chain by no-follow identity, bounded bytes,
  canonical digest, and prior-digest continuity; it never invokes F_FULLFSYNC on an existing
  O_RDONLY regular file. Still under the lock and before START or any caller-visible result, it
  runs the exact admitted writable probe in the reserved non-authoritative directory: recover only
  the two deterministic no-follow probe-slot names, remove and directory-FULLFSYNC any bounded
  stale slot, create O_CREAT|O_EXCL|O_WRONLY, write fixed bounded bytes, file-FULLFSYNC, rename,
  directory-FULLFSYNC, unlink, and directory-FULLFSYNC. A failure refuses the request, and a crash
  leaves only a bounded recoverable probe slot and no START. The process-bound StorageProfileCheck
  returned by this exact control flow is required by `begin_attempt` and its canonical fields are
  bound in START. Previously committed registry and lineage objects rely on their original
  acknowledged writer-side permanent barriers and are accepted only when their immutable bytes and
  chains revalidate; reopening does not fabricate a new durability observation. The registry has
  exactly one immutable BIND event for (catalog lineage, source reconciliation, expectation,
  policy, execution closure, compiler build, domain, storage profile, q, t_max). A copied record
  under another root fails physical identity; deletion/recreation changes inode and fails. The
  fixed namespace is `canonical_root/lineages/q` and can only be opened or created through that
  binding. A second root or second genesis cannot represent byte-identical accepted authority.
- The wrapper holds the domain registry flock for the complete
  open/recover/START/compile/revalidate/publish/TERMINAL transition. Registry BIND objects live
  under registry-events/ and START, TERMINAL, and VIOLATION objects live under
  lineages/q/chronology-events/. Each final file is named by a fixed-width sequence and its body
  digest. A valid successful snapshot has exactly START(1),TERMINAL(1),...,START(t),TERMINAL(t),
  with no other event. Each canonical event carries sequence, prior event digest, policy,
  execution_closure_sha256, compiler_build_sha256, domain, lineage, attempt, exact inputs, custody
  identity, and event-specific fields. The full runtime/tool/environment/fixed-cwd/source closures
  and repository-control proof are recomputed immediately before START and again before any attempt
  object is published; a post-START mismatch fail-stops and leaves recovery to a later qualified
  child.
- Event publication uses one deterministic staging slot in the same filesystem and under the
  exclusive lock: create a no-follow regular temp with O_CREAT|O_EXCL, write at most 16,384
  complete canonical bytes, permanently barrier the file, atomically rename to the exact
  sequence/digest final name, then permanently barrier the event directory. A crash before final
  rename exposes no committed event. A crash or barrier failure after rename but before
  acknowledgement leaves a final candidate that the next qualified process validates, permanently
  barriers as a file and directory entry, and then treats as the one committed event. Recovery
  validates a complete canonical staged object and completes its
  permanent-barrier/rename/directory-barrier sequence, or removes a malformed/incomplete
  uncommitted temp and permanently barriers the staging directory before retrying the same
  deterministic slot. Such incomplete bytes are never an attempt or evidence because no START was
  acknowledged and no permit or response crossed the interface. Recovery never truncates, rewrites,
  or deletes a committed event.
- Readers require one contiguous sequence beginning at one, exactly one no-follow regular final
  object per sequence, exact filename/body digest equality, and exact prior-digest chaining.
  Missing sequence, duplicate sequence, unexpected final name, symlink, oversize object, malformed
  canonical bytes, digest mismatch, or chain mismatch invalidates the registry or chronology. The
  virtual byte encoding is the exact canonical lineage-genesis bytes followed by the exact
  canonical event-object bytes, each component already ending in LF, concatenated in sequence order
  with no added delimiter. Before each attempt output, validate_prefix streams genesis plus
  committed events through that attempt's START and returns only the prefix digest/count bound by
  the output and receipt. finalize_history succeeds only for a unique-success tail with no
  VIOLATION and, after the matching TERMINAL is permanently published, streams genesis plus the
  now-closed validated event objects into a lock-scoped FinalHistoryView for the logical
  compile-history key. Its strict fields are custody_domain_id, custody_domain_sha256,
  custody_lineage_id, t, t_max, final_event_sha256, chronology_event_count, chronology_sha256,
  chronology_byte_count, successful_receipt_sha256, and successful_output_sha256. The final
  digest/count is absent from CoveragePlan, ContractFailure, receipt, and TERMINAL; a later
  RunEnvironment is the first persisted artifact that binds the complete view, and RunManifest,
  report, and RunSeal bind it afterward. No compile-history.jsonl or second retained
  materialization exists inside or outside CompileCustody. Optional human display writes are
  ordinary unbound CLI output and cannot satisfy any key, receipt, plan, environment, manifest, or
  claim.
- START is a committed event object before a process-bound single-use CompilePermit is returned.
  The private compiler consumes it once. No alternate public compile entry point exists.
- The wrapper builds output and receipt in one bounded staging directory. The output binds only its
  through-START chronology prefix; the receipt binds that same prefix, the output digest,
  compiler/input identities, outcome, and custody. The wrapper permanently barriers both files and
  the staging directory, atomically renames that directory to the immutable attempt/i object, and
  permanently barriers the parent before TERMINAL. Logical compile-output/i and compile-attempt/i
  keys name its two files. TERMINAL then binds both file digests and the attempt-object custody.
  Thus an acknowledged publication exposes one complete permanently flushed pair, never a canonical
  half-pair, and no pre-TERMINAL object claims the final history digest.
- On open, a tail START without TERMINAL invokes recover_interrupted_attempt while holding the
  reacquired exclusive domain lock. If a complete canonical attempt object exists, recovery
  validates it and publishes the matching TERMINAL with terminal_origin=recovery. Otherwise it
  atomically quarantines the bounded staging bytes as compile-interruption/i. A quarantine payload
  is exactly one of: the raw partial output/receipt staging pair whose combined bytes are already
  capped at 33,619,968, or one canonical empty-quarantine manifest within that same cap when no
  staging exists; it never retains both and filesystem metadata is outside the logical-byte
  measure. It then publishes a complete custody_interrupted_attempt ContractFailure plus receipt as
  attempt/i, then publishes a failure TERMINAL binding the quarantine digest and recovery observer.
  Recovery is idempotent across another crash: a complete staged TERMINAL is finished, an existing
  valid quarantine is reused, and an existing valid canonical failure pair advances to the same
  exact TERMINAL event. No committed object is overwritten. This consumes attempt i and preserves
  the original START and attempt budget. Unexpected, over-cap, symlinked, or corrupt canonical
  objects make the lineage invalid rather than being overwritten.
- Attempts 1..t-1 are typed ContractFailure, including recovered interruptions. Attempt t is the
  unique CoveragePlan success. At the successful validation snapshot there are no VIOLATION events
  and no event after TERMINAL(t). A later duplicate, extra, reused-permit, alternate-input,
  alternate-entrypoint, or post-success request first publishes a bounded VIOLATION and only then
  returns rejection, making all later validation fail rather than hiding the request. Once any
  VIOLATION exists, no later START is permitted under q; recovery of an already open START remains
  allowed so the interrupted attempt is still closed.
- finalize_history is read-only, requires the qualified lineage's exclusive lock, and returns a
  FinalHistoryView valid only while that locked handle remains open. It writes no object. D0
  inspection may render the view only as unbound CLI output. The later D1 RunEnvironment builder
  must reopen and exclusively lock the same qualified lineage, independently recompute
  FinalHistoryView rather than trust caller bytes, create and permanently publish RunEnvironment
  while retaining that lock, and release only after the environment's final parent barrier. RunSeal
  validation later reacquires the lock and requires the same final view, so a subsequent VIOLATION
  invalidates the run rather than being hidden.
- Inside the isolated child, the private API exposes initialize_domain, open_lineage,
  begin_attempt, run_attempt, recover_interrupted_attempt, validate_prefix, and finalize_history.
  The parent-facing launcher exposes only bounded initialize, compile, inspect, and finalize
  requests and never returns a live handle or permit. No API exposes a caller-selected compile
  root, authoritative reset, truncate, delete, overwrite, alternate compiler entry point, or
  lineage reuse operation. Deleting an incomplete uncommitted event temp is an internal recovery
  step and cannot delete a committed request, attempt, or event.
- Same-user/root filesystem mutation remains inside the local trust boundary. Domain/root identity,
  registry binding, inode/device/hash chains, atomic attempt objects, and final-key validation
  detect accidental or unsophisticated tamper but are not a hostile-root cryptographic guarantee.

## Mathematical Analysis

Definitions and sources
- L=12 lanes, OBSERVED SOURCE in the current expectation and fixed for v2.
- C_l is the successor expanded CasePlan count for lane l, SPECIFIED by v2:
  20,3,40,15,18,36,80,36,36,22,24,95 in expectation order. Sum C=425 CasePlans. The last lane is 19
  complete changed_identity values times five record_state values.
- G_l is the gate count by lane, SPECIFIED by v2: 3,3,4,5,4,4,7,7,7,4,5,5. Sum G=58 gates.
- D_l is the raw pre-judge evidence-member count by lane after removing controller-owned manifest,
  environment, report, and checksums from EvidenceBundle membership: 3,1,4,4,5,4,5,5,6,7,4,4. Sum
  R_DE=52 bundle/evidence membership records.
- LC_l is the pre-dispatch lane-context count: 1,2,2,1,2,2,2,2,2,1,2,2. Sum R_LC=21 lane/context
  relation records.
- LP_l is the post-gate lane-output count: 1,2,2,1,2,2,2,2,2,2,2,2. Sum R_LP=22 lane/output
  relation records. R_DE+R_LC+R_LP=95, preserving every required-artifact membership exactly once.
- O=46 required obligations and J=46 independent judge contracts, SPECIFIED structurally here;
  exact record bodies are deferred to the catalog-content gate.
- D=12 EvidenceBundleContracts, E=41 distinct raw evidence-source IDs, LC=2 lane-context artifact
  types, and LP=2 post-gate output artifact types, SPECIFIED by the successor expectation/ledger.
- Let O_l be the behavior-obligation count in lane l: 4,3,3,4,4,4,4,4,4,4,4,4, with sum O=46. Let
  m_l be the separately gated count of explicit obligation-gate pairs in lane l. Totality gives
  max(O_l,G_l)<=m_l<=O_l*G_l. Every CasePlan in lane l creates one path for each pair, so H=sum_l
  C_l*m_l.

Exact structural bounds before the catalog-content gate
```text
H_min = sum_l C_l*max(O_l,G_l)
      = 20*4 + 3*3 + 40*4 + 15*5 + 18*4 + 36*4 + 80*7 + 36*7 + 36*7 + 22*4 + 24*5 + 95*5
      = 80+9+160+75+72+144+560+252+252+88+120+475
      = 2,287 paths.
```

```text
H_max = sum_l C_l*O_l*G_l
      = 20*4*3 + 3*3*3 + 40*3*4 + 15*4*5 + 18*4*4 + 36*4*4 + 80*4*7 + 36*4*7 + 36*4*7 + 22*4*4 + 24*4*5 + 95*4*5
      = 240+27+480+300+288+576+2,240+1,008+1,008+352+480+1,900
      = 8,899 paths.
```

The raw gate-only sum sum_l C_l*G_l is 2,267 and is below H_min because core-event-replay has four
obligations but three gates. It is not an admissible exact plan under endpoint totality.

```text
R_HE_min = sum_l C_l*max(O_l,G_l)*D_l
         = 80*3 + 9*1 + 160*4 + 75*4 + 72*5 + 144*4 + 560*5 + 252*5 + 252*6 + 88*7 + 120*4 + 475*4
         = 10,693 path/evidence-member relation records.
```

```text
R_HE_max = sum_l C_l*O_l*G_l*D_l
         = 240*3 + 27*1 + 480*4 + 300*4 + 288*5 + 576*4 + 2,240*5 + 1,008*5 + 1,008*6 + 352*7 + 480*4 + 1,900*4
         = 41,883 path/evidence-member relation records.
```

```text
R_CH=R_GH=R_HNJ=H. R_GNG=R_GNP=G=58. R_DE=52, R_LC=21, and R_LP=22.
```
Therefore:
```text
20,052 <= Q = 4H+R_DE+R_HE+R_GNG+R_GNP+R_LC+R_LP <= 77,690 relation/path records.
```

Endpoint-validation work uses five endpoints per H record and two per relation record:
```text
R = 5H + 2(R_DE+R_HE+R_CH+R_GH+R_HNJ+R_GNG+R_GNP+R_LC+R_LP).
```
At the lower structural bound R>=46,965 endpoint references; at the complete bipartite lane-local
bound R<=182,077 endpoint references. The catalog-content gate freezes the exact value inside these
bounds.

The exact execution-key family is case/c; lane-context/l/a; evidence/h/e; bundle/h; judge/h;
gate-input/g; gate-result/g; judge-negative/h/n; gate-negative/g/n; plumbing-negative/g/n; and
lane-output/l/a. Therefore:
```text
K_exec = C+R_LC+R_HE+2H+2G+R_HNJ+R_GNG+R_GNP+R_LP
       = C+R_HE+3H+4G+R_LC+R_LP,
```
so 18,254<=K_exec<=69,280 before the catalog-content gate. With the virtual compile-history key, 2t
compiler receipt/output keys, run-environment, attempt-chronology, run-manifest, global report, and
run-seal, K_run=K_exec+2t+6. For 1<=t<=8, 18,262<=K_run<=69,302. The compile-history key is the
post-TERMINAL virtual stream first bound by RunEnvironment; it does not imply a distinct retained
byte copy or a digest inside CoveragePlan/receipt. The catalog gate fixes H and R_HE, and the
successful compile fixes t and the exact key count.

Current degree checks
- max D_l=7<=d_DE=16; max LC_l=2<=d_LC=2; max LP_l=2<=d_LP=2.
- max O_l*G_l=4*7=28, so paths per CasePlan<=28<=d_CH=32.
- max C_l*O_l=95*4=380, so paths per gate<=380<=d_GH=512.
- max C_l*G_l=80*7=560, so paths per obligation<=560<=d_H=1024.
- Each path/gate has exactly one negative-test mapping, satisfying all three degree-one caps.
- The catalog-content gate must allocate 58 gates across 46 obligations with every obligation
  nonempty and paths_per_obligation<=d_H=1024. The exact accepted assignment is explicitly checked
  rather than assumed.
- H_max=8,899<=path_count_max=32,768 and H<=C*d_CH=425*32=13,600.

Custody state and causal invariants
- Let b be the exact tuple (catalog lineage, source reconciliation, expectation, policy, custody
  domain, q, t_max). Registry validity requires |BIND(b)|=1 and for every open request
  Registry(b)=q. The accepted catalog fixes the domain digest, and domain validation fixes one
  canonical root identity, so a second root cannot satisfy the same b.
- The exclusive domain lock permits at most one open attempt: N_dangling_START<=1. For attempt i
  the only non-invalid state order is READY -> START_DURABLE -> ATTEMPT_OBJECT_DURABLE ->
  TERMINAL_DURABLE. Recovery maps START_DURABLE with no object to an interrupted ContractFailure
  object, or ATTEMPT_OBJECT_DURABLE to its matching TERMINAL; every transition advances and none
  returns to READY.
- A valid success contains exactly one registry BIND and 2t chronology event objects for 1<=t<=8,
  hence 2<=chronology_event_count<=16 and V=0. At most t-1<=7 recovered interruptions can precede a
  success. If attempt 8 becomes a recovered failure, q is preserved but exhausted and D0 remains
  incomplete; changing q is not an operational retry.
- Assign compile-to-run causal ranks 0 through 5 to START(t), CoveragePlan, successful receipt,
  TERMINAL(t), finalized virtual-history tuple, and RunEnvironment. Assign ranks 6 through 14 to
  lane-context, CaseResult, raw EvidenceMember, EvidenceBundle, JudgeResult, GateInputSet,
  GateResult, lane report, and lane checksums; global RunManifest/report/RunSeal extend ranks 15
  through 17. Every required predecessor edge has strictly increasing rank. CoveragePlan and
  receipt contain only the rank-0 through-START prefix, while the rank-4 final-history tuple
  includes TERMINAL and is first persisted at rank 5. Therefore a directed cycle would require
  rank(x)<rank(x), which is impossible. The compiler and run validator reject any edge outside this
  rank order.

Compiler work and memory
- Let W be complete qualified execution-closure regular-file bytes streamed during one full pass,
  N_exec its bounded filesystem-record count, U_exec its combined relative-path and symlink-target
  bytes, N_img the loaded native-image count, U_img its path bytes, A complete authority bytes
  streamed once, S cited section bytes counted with overlap multiplicity, B serialized input bytes,
  R endpoint references, P output bytes, N_repo bounded repository-control entries, and U_repo all
  repository-control relative-path, admitted control/ignore-file, and fixed Git stdout/stderr bytes
  visited in one complete pass. U_exec<=16,777,216+16,777,216=33,554,432; N_img<=512 and
  U_img<=2,097,152; U_repo components have the explicit file/path/per-command caps above and a
  policy-fixed finite command count. For each directory d, n_d<=4,096 and the sum of immediate-name
  bytes is <=4,194,304.
- The private iterative stable bottom-up merge sort performs at most n*ceil(log2(max(2,n)))
  comparisons. Define Z_exec=4,096*sum_exec_d(n_d*ceil(log2(max(2,n_d)))),
  Z_img=4,096*N_img*ceil(log2(max(2,N_img))), and Z_repo analogously as conservative
  byte-comparison visits. Therefore Z_exec<=4,096*4,096*12=201,326,592,
  Z_img<=4,096*512*9=18,874,368, and Z_repo<=4,096*32,768*12=1,610,612,736 per complete pass. These
  bounds charge 4,096 bytes to every comparison even when a shorter prefix decides it; they do not
  assume linear sorting or rely on an ambient library-sort comparison bound.
- Every successful custody path performs at most three complete execution/repository validation
  passes: before lineage access, immediately before START, and after compilation before
  publication. It also reads/hashes each authority and section byte, parses each input byte,
  validates every endpoint, and writes each output byte. Its elementary-work upper bound is
  O(3*(W+N_exec+U_exec+N_img+U_img+Z_exec+Z_img+N_repo+U_repo+Z_repo)+A+S+B+R+P). The total
  path-comparison term is at most 3*(201,326,592+18,874,368+1,610,612,736)=5,492,441,088
  byte-comparison visits. All factors, sums, and products are checked u64. No Theta or
  measured-latency claim is made.
- Execution-closure hashing and non-sort repository bytes are streamed through h_max. A depth-first
  walk may retain sorted sibling names and indices for multiple active ancestor frames; every
  frame's <=4,194,304 name bytes and <=65,536 checked-u64 logical index bytes, the image set's
  <=2,097,152 path bytes and <=8,192 index bytes, plus every retained path/target/record/normalized
  tuple, is cumulatively charged to x_max before allocation. A combination within individual caps
  but above the global arena fails before allocation: pre-START execution-closure and repository
  walks return custody_execution_closure_mismatch and custody_repository_control_unsupported
  respectively; compiler-owned catalog/plan retention returns resource_index_cap_exceeded;
  post-START closure/repository revalidation fail-stops for qualified recovery. W, U_exec, U_img,
  U_repo, Z_exec, Z_img, and Z_repo are work measures, not simultaneously retained bytes. Logical
  compiler-controlled auxiliary bytes are bounded by h_max+l_max+x_max+q_max
  = 65,536+4,194,304+33,554,432+65,536
  = 37,879,808 bytes (36.125 MiB).
This is a logical auxiliary-memory bound, not Python/interpreter RSS; it excludes the interpreter,
stack, imported module pages, allocator metadata, and the retained arena is charged before
allocation.
- Output P<=33,554,432 bytes. A<=67,108,864, S<=33,554,432, and B<=16,777,216 bytes. Checked u64
  additions and checked conversion to platform size occur before read, allocation, range
  arithmetic, or output publication.
- Empty inputs, one-past record/path/name/directory/sort caps, reversed ranges, end=file_length,
  end=file_length+1, zero-length sections, comparison-work u64 overflow, and platform-size overflow
  receive explicit boundary tests.

Custody storage bound
- At t_max=8, eight canonical output/receipt pairs consume at most
  8*(33,554,432+65,536)=268,959,744 bytes.
- In the conservative case where every START leaves a bounded staging pair before recovery, eight
  immutable interruption quarantines consume at most another 8*33,619,968=268,959,744 bytes.
- Domain record, registry genesis, one registry binding, lineage genesis, and 16 START/TERMINAL
  events consume at most 32,768+32,768+16,384+32,768+16*16,384=376,832 bytes.
- A valid successful lineage has V=0 VIOLATION events and therefore
  D_compile_valid_logical<=268,959,744+268,959,744+376,832=538,296,320 bytes, excluding filesystem
  metadata and temporary-write headroom.
- The virtual compile-history key retains zero additional bytes: validate_prefix streams the
  already counted lineage genesis and events through START into the already bounded output/receipt
  fields; after TERMINAL, finalize_history streams those same committed objects and returns the
  final view without storing their bytes again. The later RunEnvironment stores only that view's
  fields inside its separately bounded run artifact. No compile-history.jsonl is stored, and no
  pre-TERMINAL record contains the final digest/count, so there is neither an omitted duplicate
  chronology term nor a hash self-cycle.
- At most one deterministic 16,384-byte event staging file exists under the exclusive lock. A
  complete staged event is promoted to its one final object; an incomplete uncommitted temp is
  removed before the same slot is reused. Repeated publication crashes therefore do not grow valid
  retained logical custody, while physical atomic-publication headroom must include this 16,384
  bytes.
- An invalid but preserved lineage with finite V VIOLATION events adds at most 16,384*V bytes.
  There is deliberately no finite V cap compatible with the requirement to durably record every
  valid-custody duplicate/extra request; validation streams these events in O(V) time and O(1)
  event memory, and any V>0 makes D0 fail.
- The wrapper preflights the valid-lineage logical budget before START(1) and one event before
  accepting any later request. Physical free space, temporary atomic-rename headroom, and the
  qualified storage profile are observed separately. Storage exhaustion, profile drift, or
  permanent-barrier failure fails closed and cannot be represented as a successful chronology.

Sensitivity and break conditions
- If any lane CasePlan or gate cardinality changes, the expectation digest changes and the frozen
  H/R arithmetic no longer authorizes compilation; a new catalog/traceability revision and design
  review are required.
- If any required artifact changes role among raw evidence, pre-dispatch context, or post-gate
  output, R_DE/R_HE/R_LC/R_LP and the causal key set change and require a new design review. The
  compiler rejects omission, overlap, or a partition total other than 95.
- Denser obligation-gate mappings increase H, R_HE, Q, and R monotonically up to the derived
  lane-local complete bipartite bounds. The content gate must justify each pair; the compiler
  neither invents nor removes pairs.
- Increasing t_max increases worst-case valid-lineage custody linearly by one canonical
  output/receipt pair, one conservative interruption quarantine of the same bound, and two
  chronology events per added attempt. Changing the prefix/final-history binding order or moving
  the final digest into a pre-TERMINAL object is a material causal change. There is no hidden
  retry.
- Changing the OS build, dyld shared-cache UUID, authenticated-root/SIP state, isolated Python
  argv/flags/sys.path/fixed-cwd, sanitized environment, Python framework tree, native-image
  classification, Git binary/exec tree/command template/output cap, strict config parser or
  admitted-key grammar, canonical directory-sort implementation/rule/cap, repository-control
  walk/attribute-absence/ignore-binding rule or cap, authority source closure,
  compiler_build_sha256, df/diskutil/sw_vers/csrutil tool bytes, APFS device/mount/VolumeUUID,
  Internal/SolidState/Apple-Fabric facts, admitted writable F_FULLFSYNC probe algorithm/result, or
  permanent-barrier order invalidates the execution closure or CustodyDomain and requires a
  separately reviewed successor catalog. A later restoration cannot make an already interrupted
  attempt disappear, and an excluded read-only-reopen line or ordinary fsync success cannot repair
  a mismatch.
- Raising a byte/count cap is a material design change. Lower observed files do not authorize a
  runtime cap increase.
- The formulas are dimensional: W,A,S,B,P,U_exec,U_img,U_repo,D_compile are bytes;
  R,Q,H,C,G,O,J,D,E,LC,LP,V,N_exec,N_img,N_repo and n_d are counts; Z_exec,Z_img,Z_repo are
  conservative byte-comparison visits; time complexity is in elementary byte, comparison-byte,
  path-record, and endpoint-reference visits.

Failure behavior and claims
- Routine contract failure writes a complete bounded ContractFailure and leaves the source
  repositories unchanged.
- Missing, stale, extra, duplicate, orphaned, over-cap, noncanonical, or tampered authority cannot
  produce CoveragePlan.
- A CoveragePlan proves closure relative to the accepted catalog and exact source snapshot only. It
  does not prove production behavior, Adapter availability, or semantic completeness of TurnVector
  prose.
- D0 completion requires the later catalog-content gate to bind one verified CustodyDomain under
  darwin-apfs-internal-fullfsync-v1 and the exact qualified execution/compiler closure, one unique
  successful compile history within eight attempts with no VIOLATION, source_contract.matches=true
  for the paired checkout, all fixture/self-tests green in the named environment, and both
  repositories unchanged.
- D0 may report authority_compiled and structural_fixture_ready. It may not report adapter_ready,
  executable_ready for real production profiles, claim_ready, product performance, AX superiority,
  P1 evidence upgrades, or a completed real qualification run.

## Implementation and PR Sequence

Every PR starts from freshly fetched TurnVectorBenchmark origin/main after the prior PR is
squash-merged. DeepSeek V4 Flash writes source/tests only in its isolated Benchmark worktree and
performs no Git/network operations. The coordinator reviews, verifies, signs commits, pushes, opens
ready PRs only after checks, squash merges, verifies remote main/head cleanup, and then begins the
next PR. TurnVector remains read-only, with before/after status checks.

**PR 1 docs/d0-authority-design**
- Add the gate-passed D0 design document and reconciliation matrix only. No runtime behavior
  changes.

**PR 2 feat/source-reconciliation-contract**
- Add source-reconciliation schema, exact seven-record artifact, strict loader, historical/current
  Git-object verification, and tests.

**PR 3 test/owner-lifecycle-fixture**
- Add the nonclaimable same-process daemon/Device Executor fixture implementation, isolated fixture
  helpers, failure injections, the typed LaneContext execution_provenance contract, and
  LaneController's absorbing run_fixture_taint transition/claimability interlock. Add focused
  negatives proving that a non-fixture subject plus this fixture remains not_claimable_fixture and
  that missing, mismatched, or late fixture provenance fails closed. Do not rename the active
  suite/schema or change the current expectation/runner registry yet.

**PR 4 feat/implementation-expectation-v2**
- Git-rename expectation v1 to v2, add schema v3 authority binding, apply all seven successor lane
  dispositions, rename the owner-lifecycle suite/schema, switch the runner registry to the PR 3
  fixture only through its already-active fixture-taint interlock, add certification-record-v2
  without time authority, expand the certification identity matrix, rename
  lane/threshold/external-input keys in the reference fixture, update loaders/docs/CI/tests, and
  prove 12/425/58 plus exact source match. Old Worker/private-protocol naming and Certification
  Record expiry are absent from the successor TurnVector contract. Activation tests must use a
  non-fixture subject with otherwise passing results and prove the run remains
  fixture_tainted/not_claimable_fixture before CI selects v2. Use multiple independently reviewable
  commits within this PR if needed to keep every non-documentation commit at or below 500 changed
  lines.

**PR 5 feat/obligation-catalog-contract**
- Add catalog schemas, canonical JSONL loader, range/hash validation, custody-domain binding
  fields, readiness/blocker algebra, strict limits, and synthetic fixtures/tests. Do not add final
  obligation bodies.

**PR 6 feat/coverage-compiler**
- Add private compiler, exact error algebra, resource accounting, the exact raw/context/post-output
  artifact partition, causal relation validation, canonical CoveragePlan/ContractFailure output,
  and exhaustive boundary/negative tests. Bare compiler remains inaccessible from CLI and cannot
  perform Git, storage, or custody operations.

**PR 7 feat/compile-custody**
- Add the isolated authority bootstrap/launcher, canonical
  source/runtime/tool/environment/fixed-cwd closure, compiler_build identity, fail-closed
  controlled Git/repository descriptor, exact Darwin/APFS/internal-Apple-SSD storage qualification,
  permanent-barrier helper, one-time CustodyDomain initialization, root registry and unique lineage
  binding, immutable atomic event-object wrapper, atomic attempt objects, permits,
  interrupted-START/barrier/runtime-drift recovery, durable VIOLATION behavior,
  validate_prefix/finalize_history virtual streams, receipt/output custody, validator, CLI relay,
  and crash/tamper/cross-root/duplicate/post-success tests.

**Catalog content gate**
- Initialize one host-bound external CustodyDomain with the PR 7 CLI, then freeze a separate
  self-contained bundle containing that exact domain record, all 46 complete obligations, exact
  section ranges/hashes, all gate ownership, judges, raw evidence bundles, lane-context contracts,
  post-gate-output contracts, negatives, and traceability bytes. It must pass its own Mathematical
  Gate and fresh three-reviewer unanimous round before PR 8.

**PR 8 feat/current-obligation-catalog**
- Add only the passed catalog and traceability bytes plus tests that recompute every
  digest/count/relationship. No source logic change unless separately reviewed as a new candidate.

**PR 9 ci/d0-authority-closure**
- Integrate authority inspection into LaneController/CLI/CI, preserve fixture nonclaimability, run
  one external paired-checkout compile lineage, verify source_contract.matches and compile history,
  and document D0 limitations.

Each non-documentation commit must remain <=500 additions plus deletions. If a planned slice cannot
stay green and within 500, split it further without changing this design. Any need to change a
field, invariant, cap, error variant, topology disposition, count, or trust boundary returns to
this gate.

## Verification Contract

- /Users/chenyu/Documents/github/TurnVectorBenchmark/.venv/bin/python -B -m unittest discover -s
  tests -v on every PR, with dependency sync first if the worktree does not share this provisioned
  environment.
- /Users/chenyu/Documents/github/TurnVectorBenchmark/.venv/bin/python -B -m turnvector_benchmark
  inspect with the active expectation.
- Focused authority/compiler/custody tests including every error variant and all exact/one-past
  caps.
- Custody tests must prove exact isolated argv/sys.flags/sys.path/environment/fixed-cwd and
  child/Git stdout/stderr/timeout boundaries, no ambient variable or caller-cwd inheritance,
  canonical source/Python-tree/Git-tree algorithms, compiler_build recomputation, dyld
  shared-cache/native-image rules, absolute Git invocation, system/global/replace/alternate/graft
  suppression, strict local-config grammar, pre-Git include/includeIf rejection, every admitted and
  rejected config-key class, common/git-dir/worktree/index/HEAD attributes-source rejection,
  ASCII-case control-name collisions, bounded canonical .gitignore discovery/content binding and
  info/exclude binding, gitlink and assume-unchanged/skip-worktree rejection, active control-lock
  rejection, exact/one-past execution/repository
  record/path-total/per-directory-entry/per-directory-name/sort-work/config/ignore/Git-record/Git-output
  caps, no filter/alias/hook/credential/transport/config-selected executable invocation,
  repository-control before/after identity, and one-field
  add/remove/type/mode/byte/link/path/inode/version/UUID/status/env/argv/cwd/tool/source/config/index-flag/lock/ignore/attribute
  drift refusal. They must also prove copied-domain cross-root rejection, one registry binding,
  exact contiguous event enumeration, exact df/diskutil/sw_vers/csrutil parsing and tool-digest
  checks, APFS/internal/solid-state/Apple-Fabric exact and one-field-drift cases, explicit
  exclusion of storage-barrier-evidence-r5 read-only reopen lines, no F_FULLFSYNC on an existing
  O_RDONLY regular file, fresh locked O_WRONLY-file/O_RDONLY-directory profile probing before every
  START, probe-slot recovery before START, and no ordinary-fsync fallback. They must prove
  validate_prefix through START with exact digest/count, finalize_history only after TERMINAL under
  the exclusive lock, exact FinalHistoryView fields, virtual-history zero-copy/digest identity,
  absence of the final digest/count from plan/receipt/TERMINAL, rejection of a view after lock
  release, torn/complete event-stage recovery on every side of
  write/file-FULLFSYNC/rename/directory-FULLFSYNC, recovery both before and after atomic attempt
  publication, runtime or repository-control drift before START and after START, interruption
  budget consumption, fail-stop with no compiler/rejection response before permanent durability or
  under a changed closure, VIOLATION-before-reject for every reason, post-success invalidation, and
  zero hidden reset paths. D1 must separately test that RunEnvironment is the first persisted
  final-history binding and is permanently published under the same lock before any real execution.
- Artifact-causality tests must prove the 52+21+22=95 disjoint total partition, judge inputs
  contain only 41 raw evidence-source IDs, context precedes every case START, report follows every
  GateResult, and checksums excludes itself while binding every prior lane artifact.
- Controller tests must prove run_fixture_taint starts clean, transitions before the first CasePlan
  START, is absorbing, binds every final run artifact, and takes precedence over a real/non-fixture
  subject, all-passing lanes, exact source match, and later authority readiness. Reference fixture
  runs remain not_claimable_fixture.
- git diff --check, final diff review, signed-commit verification, per-commit <=500 checker, and
  both repositories' status before/after paired checks.
- For PR 4 and PR 9, paired inspect against /Users/chenyu/.codex/worktrees/da20/TurnVector must
  report source_contract.matches=true, observed revision eedad5f, exact or accepted descendant
  relation as applicable, 12/425/58, and clean target.
- PR checks and remote head SHA must be verified before each squash merge. New work begins only
  after origin/main contains the verified squash.

## Alternatives Considered and Rejected

1. Seven filename substitutions under turnvector-implementation-v1. Rejected because six mappings
   materially change topology and the old Worker/IPC lane would cite contrary in-process authority.
2. Copy v1 and add a full v2 duplicate. Rejected because it adds about 1,200 non-documentation
   lines, violates the commit cap, and creates two active normative contracts. Git history
   preserves v1.
3. Remove the Worker lane until E01. Rejected because missing required coverage cannot become
   skipped. A nonclaimable current-topology fixture preserves structural coverage while real
   Adapter availability remains blocked.
4. Treat every architecture ledger row as one Benchmark obligation. Rejected because 194
   implementation-planning rows are not 194 independent qualification claims. The 46 behavior
   obligations remain claim-oriented; modules/seams/schemas are separately enumerated authority
   entities and blockers.
5. Infer obligations or gate ownership from source prose. Rejected because deletion could remain
   internally consistent. The separately gated catalog and ledger are authority.
6. Use AX constants, baselines, or self-reported metrics. Rejected by repository independence and
   evidence policy.
7. Use a new public plugin seam or one lane per TurnVector module. Rejected because it exposes
   implementation topology and weakens the LaneController deep interface.
8. Let the CLI call CoverageCompiler directly. Rejected because it permits hidden attempts outside
   CompileCustody.
9. Claim a Python whole-process memory bound from logical caps. Rejected; the design states only
   logical compiler-owned bytes and records runtime overhead separately.
10. Fix the transient xctrace fixture in D0. Rejected as unrelated; 93/93 baseline is green and
    scope remains authority reconciliation.
11. Let every compile invocation choose a new external root. Rejected because the same accepted q
    could obtain independent histories. One catalog-bound physical CustodyDomain and registry is
    required.
12. Treat a dangling START as permanent lineage loss. Rejected because an ordinary process/host
    crash would exhaust the only accepted q without consuming a typed attempt. Atomic attempt
    publication plus lock-proven recovery preserves a failure or the already complete outcome.
13. Put manifest, environment, report, and checksums into every EvidenceBundle. Rejected because
    controller context is lane-level and report/checksums are post-gate; the latter creates a
    judge/gate/report digest cycle. The exact 52 raw, 21 context, and 22 post-output partition
    preserves all 95 requirements causally.
14. Preserve Certification Record issued_at/expires_at as generic Benchmark policy. Rejected for
    the accepted successor TurnVector expectation because current authority explicitly forbids
    invented wall-clock Certification Record expiry. The separate peer-comparison performance
    evidence contract may retain its own temporal validity semantics.
15. Rename only worker_build and protocol_capability one-for-one. Rejected because the lane claims
    exact applicability while omitting other exact Capability Key and Certification Envelope
    determinants. The complete 19-value changed_identity domain remains within existing caps and
    makes the matrix test the stated requirement.
16. Repair torn JSONL by truncating the tail. Rejected because truncation can erase a START,
    TERMINAL, VIOLATION, or BIND request. Canonical immutable event objects make commit atomic and
    leave no mutable authoritative tail.
17. Preserve a second compile-history.jsonl file for convenience. Rejected because it duplicates
    already authoritative events, enlarges custody, and creates another crash-consistency surface.
    The logical key is a zero-copy validated virtual stream.
18. Treat fsync or F_BARRIERFSYNC as acknowledged power-loss durability. Rejected by the named
    macOS syscall contract. The only admitted profile uses F_FULLFSYNC on each complete file and
    destination directory and refuses every unsupported or drifted storage environment.
19. Bind only sys.executable and imported authority .py files. Rejected because the Xcode launcher
    resolves a separate mutable Python framework, native modules and system-cache images affect
    semantics, /usr/bin/git is only a selector shim, Git consults repository control state, and
    ambient environment can alter both runtimes. The isolated full closure is revalidated on both
    sides of execution.
20. Bind repository config and attribute bytes while permitting includes or filters. Rejected
    because a bound file can select unbound transitive config or executable behavior. The supported
    profile rejects every attributes source, include/includeIf directive, gitlink, and
    command-reachable executable/transport-affecting config key before Git runs; the closed
    metadata-only set is admitted only because the fixed read-only grammar cannot reach it.

## Risks and Unresolved Work

- The later catalog-content review is substantial and may find that one or more current behavior
  obligations must split or merge. Changing O=46 is a material revision, not a compiler workaround;
  selecting justified many-to-many gate pairs within the frozen bounds is expected catalog work.
- backend_interface_v1 remains unavailable until E01. D0 cannot freeze a real native Adapter or
  claim production execution.
- The owner-lifecycle fixture can validate Benchmark plumbing only; it is never production evidence.
- Python logical arena accounting does not bound interpreter RSS. Operational execution limits and
  observed RSS remain later hardening/evidence work.
- Immutable event-object custody is inside a local same-user/root trust boundary. It is auditable,
  not hostile-root secure.
- The accepted CustodyDomain is host/filesystem-bound. Moving or recreating its root invalidates
  that catalog and requires a separately gated successor preserving the predecessor
  registry/chronology; this trades portability for no-hidden-reset evidence.
- The accepted execution and storage profiles are deliberately narrow: the exact Xcode Python/Git
  trees, system cache, fixed cwd, repository controls, Darwin build, and local internal APFS/Apple
  Fabric SSD are supported only while every source/runtime/tool/environment/repository/storage fact
  and F_FULLFSYNC probe revalidates. Xcode, Python, Git, OS-cache, cwd, config, attributes,
  environment, external, network, virtual, changed-OS, or filesystem drift is unsupported rather
  than silently accepted.
- A valid-custody stream of duplicate requests can grow the VIOLATION log without a finite count
  cap and cause storage denial. It cannot produce a false successful chronology; operational access
  remains inside the trusted local boundary.
- Current TurnVector origin/main may advance. Any change to listed authority bytes invalidates the
  catalog/plan; unrelated descendant changes remain recorded but do not silently alter authority.
- Exact D1-D5 schemas and numeric limits are outside this proposal and require later gates.

## Permitted Claim

The following is the passed-proposal claim text, quoted as a permitted statement if this proposal
passes; it is not current implementation evidence. This document carries no implementation,
product, performance, adapter-ready, executable-ready, or claim-ready evidence.

> "The D0 implementation contract is internally coherent for a versioned in-process TurnVector
> qualification expectation with 12 lanes, 425 CasePlans, and 58 gates; strict seven-source
> reconciliation; a complete current Certification identity matrix without wall-clock record
> expiry; a separately gated 46-obligation catalog; a causal 52 raw/21 context/22 post-output
> artifact partition; bounded CoverageCompiler; and one domain-anchored eight-attempt immutable
> event-object CompileCustody executed only inside a fully bound isolated
> Python/Git/source/environment closure, whose acknowledged publications use a qualified
> Darwin/APFS/internal-Apple-SSD F_FULLFSYNC profile and whose history key is a zero-copy virtual
> stream. It authorizes the nine sequential D0 PR slices and no TurnVector edit or product claim.
> D0 itself remains incomplete until the catalog-content gate binds one qualified CustodyDomain
> plus exact execution/compiler closure and the unique paired-checkout compile success passes with
> no violation."
