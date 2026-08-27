# Obligation Catalog Contract (PR 5)

This document records the accepted PR 5 obligation-catalog contract. The
authoritative decisions are the PR 5 contract amendment, proposal revision
`b068126b65ddaf8ea3f0f8ec9d1ced7409c3f545662864891e937d15cd8654b4`, accepted
by review round `TVB-AX-DETAIL-DESIGN-20260826-PR5A-R1-20260827T081347Z`
(unanimous gpt-5.6-sol/max PASS and Mathematical Gate PASS), applied on top of
`docs/D0-AUTHORITY-DESIGN.md` (accepted design revision
`3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a`). Every
rule below is an accepted decision; none is a flagged interpretation.

PR 5 does **not** add final obligation bodies, and no accepted catalog exists
in this PR. The reserved accepted path `authority/obligation-catalog-v1.jsonl`
is intentionally left empty; synthetic fixtures live under
`tests/fixtures/obligation-catalog/` and are clearly marked non-authority. PR 5
accepts contract implementation and synthetic-fixture verification only; it
makes no TurnVector product, benchmark-claimability, custody-durability,
final catalog-content, or D1-D5 claim.

## Scope

- `schemas/obligation-catalog-header-v1.schema.json` — validates one header
  record (schema family `turnvector.benchmark.obligation-catalog.v1`).
- `schemas/obligation-record-v1.schema.json` — validates one obligation
  record in the same family.
- `turnvector_benchmark/obligation_catalog.py` — strict canonical JSONL
  loader for the catalog file.
- `turnvector_benchmark/obligation_sources.py` — no-follow bounded source
  file/range/hash verification against a caller-provided source root.
- `turnvector_benchmark/compile_limits.py` — frozen checked-u64 CompileLimits
  contract with monotonic fail-closed test injection.
- `turnvector_benchmark/canonical.py` — shared strict canonical encoding,
  no-follow bounded IO, and typed field validators reused by later PRs.

## Header

Exact fields: `kind=catalog`, `schema_version`, `id`, `profile_id`,
`lineage_id`, `predecessor`, `design_gate_revision`,
`source_reconciliation_sha256`, `expectation_sha256`,
`compile_custody_policy_sha256`, `custody_domain_id`, `custody_domain_sha256`,
`compile_custody_lineage_id`, `t_max`, `required_obligation_count`,
`record_count`.

Frozen constants enforced by the loader:

| Field | Frozen value |
| --- | --- |
| `profile_id` | `turnvector-implementation-v2` |
| `compile_custody_lineage_id` | `tvb-qualification-d0-catalog-v1` |
| `t_max` | 8 |
| `required_obligation_count` | 46 |

Binding data preserved verbatim: `lineage_id`, `predecessor`,
`custody_domain_id`, `custody_domain_sha256`, and the three referenced
artifact digests. Mutating any of them changes the catalog file digest.

### `record_count` is the total record count

`record_count` is the total number of JSONL records **including the header**,
so `record_count = 1 + N` where N is the number of obligation records after
the header. A catalog with exactly the 46 required records and no optional
records declares `record_count = 47`; the synthetic fixture has one optional
record and declares 48. The body-only interpretation is rejected because it
duplicates the obligation-list length; the total-record interpretation binds
the physical JSONL structure. The loader enforces
`record_count == 1 + len(obligation records)`, and the header schema constrains
`record_count` to a u64 with minimum 1 and maximum
`18446744073709551615`.

### `predecessor` names the custody-history predecessor pair

`predecessor` is `null` for a lineage-genesis header, or exactly one strict
object with exactly two fields:

```json
{
  "compile_custody_lineage_id": "<identifier>",
  "chronology_sha256": "<lowercase 64-hex SHA-256>"
}
```

The object names the predecessor CompileCustody lineage `q` and the SHA-256
field `chi` from its finalized `FinalHistoryView`, using the accepted
`FinalHistoryView` field name `chronology_sha256`. Unknown or missing fields
are rejected, `compile_custody_lineage_id` must match the stable identifier
grammar, `chronology_sha256` must be a lowercase SHA-256 digest, and a
predecessor `compile_custody_lineage_id` equal to the current header
`compile_custody_lineage_id` is rejected: byte-identical authority cannot
change `q`, and a successor `q` remains a separately gated material authority
change. PR 5 validates shape, canonical encoding, digest grammar, and
current-vs-predecessor `q` inequality only; it cannot prove that `null` is
historically genesis or that a referenced chronology exists and is preserved.
CompileCustody (PR 7) and the catalog-content gate own those facts.

`design_gate_revision` is format-validated only; the accepted catalog binds its
own (future) gate revision, not this proposal revision.

## Obligation records

Exact fields: `kind=obligation`, `id`, `required`, `claim_class`,
`source_path`, `source_file_sha256`, `section_start`, `section_end`,
`section_sha256`, `module_ids`, `seam_id`, `observable_seam`,
`evidence_grade`, `invalidation_rule`, `lane_id`, `behavior_case_id`,
`readiness_status`, `blocker_ids`, `design_gate_revision`.

### Field domains

Fields named `id`, `*_id`, and `*_ids` elements use the repository stable
identifier grammar `^[a-z0-9][a-z0-9._-]*$`: header `id`, `lineage_id`,
`custody_domain_id`, current and predecessor `compile_custody_lineage_id`,
obligation `id`, `module_ids` elements, `seam_id`, `lane_id`,
`behavior_case_id`, and `blocker_ids` elements.

`claim_class`, `observable_seam`, `evidence_grade`, and `invalidation_rule`
are nonempty Unicode prose strings whose exact vocabulary/content is deferred
to the catalog-content gate; they do **not** use identifier grammar and carry
**no per-field byte maximum**. Each canonical catalog is bounded as a whole by
`largest_single_serialized_parser_input_max = 4,194,304` bytes and
`serialized_input_bytes_total_max = 16,777,216` bytes, so the tighter
applicable file cap is 4,194,304 bytes. Avoiding per-field `maxLength` also
avoids pretending JSON Schema character counts are UTF-8 byte counts.

`module_ids` is required, contains at least one identifier, and contains no
duplicate; an obligation without a module owner is not compiler-ready
authority. `blocker_ids` contains identifiers, contains no duplicate, and may
be empty only in the readiness states below.

### Byte-range convention

`section_start`/`section_end` are 0-based byte offsets; the section is the
half-open range `[section_start, section_end)`. `section_end` may equal the
file length but must never exceed it. Zero-length and reversed ranges are
rejected. `source_file_sha256` is over the entire source file; `section_sha256`
is over the exact nonempty range bytes.

### Readiness/blocker algebra is a four-state truth table

The abstract product state has 2 (required) x 4 (readiness) x 2 (blocker
emptiness) = 16 combinations. The accepted truth table admits exactly four:

| `required` | `readiness_status` | `blocker_ids` |
| --- | --- | --- |
| `true` | `design_ready` | empty |
| `true` | `adapter_blocked` | nonempty |
| `true` | `environment_blocked` | nonempty |
| `false` | `intentionally_out_of_scope` | empty |

Every other combination is invalid and fails closed. A blocked required record
remains in O_p and counts toward 46; an out-of-scope record is outside O_p. A
blocked status with no blocker is rejected because it creates no auditable
reason; blockers on `design_ready` or `intentionally_out_of_scope` are
rejected because they encode contradictory readiness semantics. Required
records never disappear because they are blocked: blocked records still count
toward the frozen 46 and the per-lane counts.

### Exact count and identity rules

- `required_obligation_count` must equal 46 and the actual count of
  `required=true` records.
- Per-lane required counts are exact in the successor expectation's lane
  order: `[4,3,3,4,4,4,4,4,4,4,4,4]` (sum 46).
- Obligation `id` is unique and strictly increasing after the header; no
  obligation `id` equals the header `id`.
- The pair `(lane_id, behavior_case_id)` is unique across **all** obligation
  records, including optional records. One behavior identity cannot carry both
  a required and an out-of-scope obligation or multiple competing authority
  records.
- Every obligation `design_gate_revision` must exactly equal the header
  `design_gate_revision`; mixing records reviewed under different
  catalog-content revisions is invalid.
- PR 5 validates the exact lane set and required per-lane counts. PR 6 joins
  required pairs to expectation v2 and proves exact behavior-set closure; PR 5
  does not infer that cross-file join.

## JSON Schema and runtime divide responsibilities without divergence

- Header schema: a `u64` definition with minimum 0 and maximum
  `18446744073709551615`; `record_count` uses it plus minimum 1; `predecessor`
  is `null` or the exact two-field object above; `compile_custody_lineage_id`,
  `id`, and `lineage_id` use the identifier pattern; all digest fields use the
  SHA-256 pattern.
- Obligation schema: `u64` minimum/maximum, exact required properties,
  identifier patterns, `module_ids` `minItems=1`/`uniqueItems=true`,
  `blocker_ids` `uniqueItems=true`, the exact 12-lane `enum`, nonempty
  free-text fields without `maxLength`, and `if/then` constraints encoding the
  complete four-state readiness truth table (including blocker
  empty/nonempty).
- Runtime enforces every local schema rule before returning typed values and
  remains authoritative for the comparisons JSON Schema cannot express here:
  `section_start < section_end`, total/header counts, per-lane counts,
  sorted/unique cross-record identities, pair uniqueness, equal
  `design_gate_revision`, and current-vs-predecessor `q` inequality.
- A file passing one record schema alone is not an accepted catalog; schema
  tests explicitly assert this responsibility split.

## Canonical encoding

The catalog is canonical JSONL: strict UTF-8 without BOM; exactly one compact
JSON object per line; recursively lexical object keys; ASCII escaping; LF
after every line including the last; header first; remaining records sorted
by stable id. The loader rejects duplicate keys, unknown fields, floats,
NaN/Infinity, unpaired surrogate escapes, noncanonical escapes, blank lines,
CR/LF, missing final LF, invalid UTF-8, BOM, non-object lines, and oversize
inputs. Resource caps are enforced at load (catalog file bytes, serialized
input bytes, record count, obligation count) and at source verification.

## CompileLimits

`CompileLimits.frozen()` carries every limit from the accepted design's
"Compile Limits" section (docs/D0-AUTHORITY-DESIGN.md, accepted design
revision `3aa2b1911970c86e1cce6d7a3d55f26279b6e76b5fa17aa74aa704beaf01d28a`)
unchanged (68 fields, all positive checked u64). A directly constructed test
limit vector `L` is valid only when every field is a positive u64 and
`1 <= L_i <= F_i` for all 68 fields, where `F` is the frozen vector; any
component above `F_i` is rejected at construction with `ContractError`.
Loader/source-verifier injection of a componentwise smaller `L` is permitted
only as fail-closed testability: it can reject an input accepted under `F` but
can never admit an input rejected under `F`. Accepted production compilation
must use and bind exactly `F`; PR 6 owns the exact-frozen equality check at
the compiler boundary, and `CompileLimits.is_frozen()` exposes the clear
equality check here. Unrestricted custom limits are rejected because they
allow a caller to bypass accepted safety caps.

PR 5 enforces the compile-input/catalog/source/range/path caps the loader and
verifier need: `largest_single_serialized_parser_input_max`,
`serialized_input_bytes_total_max`, `catalog_record_count_max`,
`obligation_count_max`, `authority_file_bytes_max`,
`authority_file_count_max`, `authority_total_bytes_max`,
`authority_section_count_max`, `authority_section_bytes_total_max`,
`path_count_max`, `execution_closure_path_bytes_max`, and
`execution_closure_path_bytes_total_max`. All arithmetic is checked u64.

Note: `catalog_record_count_max` (512) and `path_count_max` (32,768) and
`authority_section_count_max` (1024) are not independently reachable at PR 5
because `obligation_count_max` (256) binds first; the checks are present and
the arithmetic is unit-tested.

## Source verification

`verify_obligation_sources(catalog, source_root, limits)` verifies every
obligation record (required and out-of-scope). The root must be a real
directory; every path component is lstat'ed and opened with `O_NOFOLLOW`
relative to its parent descriptor, so escape via `..`, symlinked components,
symlinked files, and swap/truncation races fail closed. Each distinct file is
read once (bounded) for `source_file_sha256`; each range is re-read with
identity revalidation for `section_sha256`. The root pathname and
device/inode/uid/gid/mode identity are revalidated at the end of the pass.

## Non-goals

- No final obligation bodies; no accepted catalog at
  `authority/obligation-catalog-v1.jsonl`.
- No expectation/reconciliation digest cross-checking (PR 6 compiler join).
- No CompileCustody predecessor-history existence/preservation validation
  (PR 7) and no attempt-history semantics.
- No CompileCustody or CoverageCompiler behavior (PR 6/7).
- No memory/retained-arena claim: PR 6 must charge compiler-retained
  structures to `logical_retained_index_arena_max` before accepted
  compilation.
