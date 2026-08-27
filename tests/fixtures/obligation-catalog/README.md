# Synthetic Obligation-Catalog Fixtures

These fixtures are **synthetic test data for PR 5 only**. They are NOT
accepted TurnVectorBenchmark authority:

- The synthetic catalog is NOT placed at `authority/obligation-catalog-v1.jsonl`
  (the reserved path for the later catalog-content gate).
- The source files under `source/` are synthetic content, not TurnVector
  authority. Every file is marked `SYNTHETIC FIXTURE SOURCE - NOT ACCEPTED
  TURNVECTOR AUTHORITY`.
- Digests, identifiers, and custody-domain values are synthetic placeholders
  that bind nothing real.

Layout:

- `generate_fixture.py` — deterministic generator (regenerate with
  `.venv/bin/python -B tests/fixtures/obligation-catalog/generate_fixture.py`).
- `synthetic-catalog-v1.jsonl` — canonical JSONL: 1 header + 46 required
  obligations + 1 `intentionally_out_of_scope` record (48 records).
- `source/` — synthetic source files cited by the catalog; verify with
  `verify_obligation_sources(catalog, source/ )`.

The generator computes `source_file_sha256` over each whole source file and
`section_sha256` over each exact half-open byte range, so the committed
fixtures are internally consistent.
