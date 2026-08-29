"""Pure in-memory CoverageCompiler production and tests-only entry points.

The module is the dispatcher for the frozen six-phase compiler.  Parsing,
accounting, relation derivation, and immutable output values remain in their
own authority modules; this file fixes their ordering and translates every
observable rejection into the 48-variant ContractFailure algebra.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from turnvector_benchmark.compile_limits import CompileLimits as _CompileLimits
from turnvector_benchmark.compile_limits import checked_add
from turnvector_benchmark.core import ContractError

from .authority_snapshot import (
    AuthoritySnapshotValue,
    authority_snapshot_value,
)
from .compile_permit import CompilePermit as _CompilePermit
from .compiler_accounting import (
    HASH_BUFFER_CHARGE,
    SHA256_STATE_CHARGE,
    W_B,
    Accounting,
    OutputSink,
    PhaseBScratch,
    RetainedArena,
    SharedHashBuffer,
)
from .compiler_inputs import (
    CompilerInputs,
    compute_input_set_sha256,
    sha256_bound_bytes,
)
from .compiler_relations import (
    DerivedRelations,
    PhaseBResult,
    RelationValidationError,
    derive_relations,
    digest_relations,
    enumerate_expected_keys,
    validate_relations,
)
from .contract_failure import ContractFailure, build_contract_failure
from .contract_json import (
    InvalidCanonicalJson,
    ParsedJson,
    canonical_json_bytes,
    is_canonical_json,
    is_canonical_jsonl,
    parse_json_object,
    parse_jsonl_records,
)
from .coverage_plan import (
    AUTHORITY_PATHS,
    LIMITATIONS,
    CoveragePlan,
    build_coverage_plan,
)
from .errors import CompilerInternalError, CompilerPreconditionViolation
from .traceability_ledger import TraceabilityLedgerValue, traceability_ledger_value

_RESULT = Union[CoveragePlan, ContractFailure]
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class _StageFailure:
    variant: str
    pointers: Tuple[str, ...]
    accounting: Accounting


@dataclass(frozen=True)
class _Parsed:
    snapshot_json: ParsedJson
    reconciliation_json: ParsedJson
    expectation_json: ParsedJson
    catalog_json: Tuple[ParsedJson, ...]
    ledger_json: ParsedJson
    snapshot: Mapping[str, Any]
    reconciliation: Mapping[str, Any]
    expectation: Mapping[str, Any]
    catalog_header: Mapping[str, Any]
    catalog_records: Tuple[Mapping[str, Any], ...]
    ledger: Mapping[str, Any]
    snapshot_value: AuthoritySnapshotValue
    ledger_value: TraceabilityLedgerValue


@dataclass
class _DerivedCounters:
    r_gng: int = 0
    r_gnp: int = 0
    h: int = 0
    r_ch: int = 0
    r_gh: int = 0
    r_hnj: int = 0
    r_he: int = 0

    def accounting(self, base: Accounting, m: int, arena_value: int) -> Accounting:
        relation_count = (
            199 + m + self.r_gng + self.r_gnp + self.r_ch + self.r_gh
            + self.r_hnj + self.r_he + self.h
        )
        endpoint_count = (
            398 + 2 * m + 2 * self.r_gng + 2 * self.r_gnp
            + 5 * self.h + 2 * self.r_ch + 2 * self.r_gh
            + 2 * self.r_hnj + 2 * self.r_he
        )
        return replace(
            base,
            entity_count=852 + self.h,
            relation_record_count=relation_count,
            endpoint_reference_count=endpoint_count,
            path_count=self.h,
            logical_arena_byte_count=arena_value,
            output_byte_count_attempted=0,
        )

    def admitted(self, label: str) -> None:
        if label == "gate_aggregate_negative":
            self.r_gng += 1
        elif label == "gate_plumbing_negative":
            self.r_gnp += 1
        elif label == "enforcement_path":
            self.h += 1
        elif label == "path_case":
            self.r_ch += 1
        elif label == "path_gate":
            self.r_gh += 1
        elif label == "path_judge_negative":
            self.r_hnj += 1
        elif label == "path_evidence":
            self.r_he += 1
        else:
            raise CompilerInternalError("unknown Phase C allocation label")


def _diagnostics(variant: str, pointers: Iterable[str]) -> Tuple[str, ...]:
    return tuple("%s|%s|%s" % (variant, pointer, variant) for pointer in pointers)


def _failure_inputs(payload: Any) -> Dict[str, Any]:
    return {
        "source_reconciliation_path": AUTHORITY_PATHS["source_reconciliation_path"],
        "source_reconciliation_sha256": payload.source_reconciliation_sha256,
        "expectation_path": AUTHORITY_PATHS["expectation_path"],
        "expectation_sha256": payload.expectation_sha256,
        "catalog_path": AUTHORITY_PATHS["catalog_path"],
        "catalog_sha256": payload.catalog_sha256,
        "traceability_path": AUTHORITY_PATHS["traceability_path"],
        "traceability_sha256": payload.traceability_sha256,
        "authority_snapshot_sha256": payload.authority_snapshot_sha256,
        "compile_limits_sha256": payload.compile_limits_sha256,
        "input_set_sha256": payload.input_set_sha256,
    }


def _prefix(payload: Any) -> Dict[str, Any]:
    return {
        "custody_domain_id": payload.custody_domain_id,
        "custody_domain_sha256": payload.custody_domain_sha256,
        "custody_lineage_id": payload.custody_lineage_id,
        "attempt": payload.attempt,
        "t_max": payload.t_max,
        "start_event_sha256": payload.start_event_sha256,
        "chronology_prefix_sha256": payload.chronology_prefix_sha256,
        "chronology_prefix_byte_count": payload.chronology_prefix_byte_count,
        "compiler_build_sha256": payload.compiler_build_sha256,
        "execution_closure_sha256": payload.execution_closure_sha256,
        "compile_custody_policy_sha256": payload.compile_custody_policy_sha256,
        "input_set_sha256": payload.input_set_sha256,
    }


def _materialize_failure(payload: Any, failure: _StageFailure) -> ContractFailure:
    fields = _prefix(payload)
    fields.update(
        inputs=_failure_inputs(payload),
        observed=asdict(failure.accounting),
        variant=failure.variant,
        diagnostics=_diagnostics(failure.variant, failure.pointers),
    )
    return build_contract_failure(**fields)


def _compile_limits_sha256(limits: _CompileLimits) -> str:
    value = {
        "limits": {
            name: getattr(limits, name)
            for name in limits.__dataclass_fields__
        },
        "schema_version": "turnvector.benchmark.compile-limits.v1",
    }
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"
    return hashlib.sha256(encoded).hexdigest()


def _pointer(base: str, path: Sequence[Any], leaf: Optional[str] = None) -> str:
    tokens = [base]
    tokens.extend(str(item) for item in path)
    if leaf is not None:
        tokens.append(leaf)
    escaped = [token.replace("~", "~0").replace("/", "~1") for token in tokens]
    return "/" + "/".join(escaped)


def _top_level_shape(parsed: Mapping[str, Any], required: Sequence[str], base: str) -> Optional[str]:
    if not isinstance(parsed, Mapping):
        return "/" + base
    required_set = set(required)
    for key in parsed:
        if key not in required_set:
            return _pointer(base, (), str(key))
    for key in required:
        if key not in parsed:
            return "/" + base
    return None


def _classify_contract_error(error: ContractError, default_pointer: str) -> Tuple[str, str]:
    text = str(error).lower()
    structural = (
        "missing required", "unknown field", "unknown fields", "must be an object",
        "must be an array", "must be a string", "must be a boolean",
        "must be an integer", "inexact field set",
    )
    if any(fragment in text for fragment in structural):
        return "authority_unknown_field", default_pointer
    return "authority_invalid_identifier", default_pointer


def _catalog_bytes(buffer: Any) -> bytes:
    if type(buffer) is bytes:
        return buffer
    try:
        return buffer.tobytes()
    except (AttributeError, TypeError, ValueError) as error:
        raise CompilerInternalError("admitted catalog buffer cannot be traversed") from error


def _parse_phase_a(inputs: CompilerInputs, base: Accounting, arena: RetainedArena) -> Union[_Parsed, _StageFailure]:
    named = tuple(inputs.iter_named())
    parsed_objects: List[ParsedJson] = []
    catalog_parsed: Tuple[ParsedJson, ...] = ()
    for index, (name, ref) in enumerate(named):
        try:
            if name == "obligation_catalog":
                catalog_parsed = parse_jsonl_records(_catalog_bytes(ref.buffer), name)
            else:
                parsed_objects.append(parse_json_object(ref.buffer, name))
        except InvalidCanonicalJson:
            accounting = replace(base, logical_arena_byte_count=arena.high_water)
            return _StageFailure(
                "authority_invalid_canonical_json", ("/" + name,), accounting
            )

    snapshot_json, reconciliation_json, expectation_json, ledger_json = parsed_objects
    snapshot = snapshot_json.value
    reconciliation = reconciliation_json.value
    expectation = expectation_json.value
    ledger = ledger_json.value
    catalog_values = tuple(record.value for record in catalog_parsed)
    if not catalog_values:
        return _StageFailure(
            "authority_invalid_canonical_json", ("/obligation_catalog",),
            replace(base, logical_arena_byte_count=arena.high_water),
        )
    header = catalog_values[0]
    records = catalog_values[1:]

    # Stage 2 owns strict outer shapes before duplicate and value grammar.
    shapes = (
        (snapshot, ("schema_version", "repository", "revision", "observed_head",
                    "revision_relation", "ancestry_verified", "clean_required", "clean",
                    "source_files", "section_records", "historical_objects",
                    "repository_control"), "authority_snapshot"),
        (reconciliation, ("schema_version", "id", "design_gate_revision",
                          "predecessor_expectation", "successor_expectation",
                          "target_source", "mappings"),
         "source_reconciliation"),
        (expectation, ("schema_version", "id", "description", "source_contract",
                       "authority", "certification_policy", "lanes"),
         "benchmark_expectation"),
        (ledger, ("schema_version", "id", "profile_id", "design_gate_revision",
                  "predecessor", "cardinalities", "binds", "entities"),
         "traceability_ledger"),
    )
    for value, required, name in shapes:
        issue = _top_level_shape(value, required, name)
        if issue is not None:
            return _StageFailure(
                "authority_unknown_field", (issue,),
                replace(base, logical_arena_byte_count=arena.high_water),
            )
    if not isinstance(header, Mapping) or any(not isinstance(record, Mapping) for record in records):
        return _StageFailure(
            "authority_unknown_field", ("/obligation_catalog",),
            replace(base, logical_arena_byte_count=arena.high_water),
        )

    # Stage 3 duplicate-key sweep in exact document order.
    duplicate_sets: Tuple[Tuple[str, Sequence[ParsedJson]], ...] = (
        ("authority_snapshot", (snapshot_json,)),
        ("source_reconciliation", (reconciliation_json,)),
        ("benchmark_expectation", (expectation_json,)),
        ("obligation_catalog", catalog_parsed),
        ("traceability_ledger", (ledger_json,)),
    )
    duplicate_pointers: List[str] = []
    for name, documents in duplicate_sets:
        for ordinal, document in enumerate(documents):
            for duplicate in document.duplicate_keys:
                path: Tuple[Any, ...] = duplicate.path
                if name == "obligation_catalog":
                    path = (ordinal,) + path
                duplicate_pointers.append(_pointer(name, path, duplicate.key))
    if duplicate_pointers:
        return _StageFailure(
            "authority_duplicate_key", tuple(duplicate_pointers),
            replace(base, logical_arena_byte_count=arena.high_water),
        )

    # Stage 4 typed value construction.  The private values intentionally do
    # not perform dispatcher-owned order/join checks.
    try:
        snapshot_value = authority_snapshot_value(snapshot)
        ledger_value = traceability_ledger_value(ledger)
        if reconciliation.get("schema_version") != "turnvector.benchmark.source-reconciliation.v1":
            raise ContractError("source reconciliation schema const is invalid")
        if expectation.get("schema_version") != "turnvector.benchmark.expectation.v3":
            raise ContractError("expectation schema const is invalid")
        if header.get("kind") != "catalog" or header.get("schema_version") != "turnvector.benchmark.obligation-catalog.v1":
            raise ContractError("catalog header const is invalid")
        if header.get("profile_id") != "turnvector-implementation-v2":
            raise ContractError("catalog profile const is invalid")
        if header.get("compile_custody_lineage_id") != "tvb-qualification-d0-catalog-v1":
            raise ContractError("catalog lineage const is invalid")
        if header.get("t_max") != 8 or header.get("required_obligation_count") != 46:
            raise ContractError("catalog numeric const is invalid")
    except ContractError as error:
        variant, pointer = _classify_contract_error(error, "/authority_snapshot")
        return _StageFailure(
            variant, (pointer,), replace(base, logical_arena_byte_count=arena.high_water)
        )

    # Stage 5 closed scalar-id uniqueness domains.
    duplicate_ids: List[str] = []
    catalog_ids = [header.get("id")] + [record.get("id") for record in records]
    _collect_duplicate_ids(catalog_ids, "obligation_catalog", (), duplicate_ids)
    lanes = expectation.get("lanes", ())
    if isinstance(lanes, list):
        _collect_duplicate_ids([item.get("id") for item in lanes if isinstance(item, Mapping)],
                               "benchmark_expectation", ("lanes",), duplicate_ids)
        for lane_index, lane in enumerate(lanes):
            if not isinstance(lane, Mapping):
                continue
            for field in ("cases", "gates", "matrices"):
                values = lane.get(field, ())
                if isinstance(values, list):
                    _collect_duplicate_ids(
                        [item.get("id") for item in values if isinstance(item, Mapping)],
                        "benchmark_expectation", ("lanes", lane_index, field), duplicate_ids,
                    )
    entities = ledger.get("entities", {})
    if isinstance(entities, Mapping):
        _collect_duplicate_ids(entities.get("judge_ids", ()), "traceability_ledger",
                               ("entities", "judge_ids"), duplicate_ids)
        _collect_duplicate_ids(entities.get("evidence_source_ids", ()), "traceability_ledger",
                               ("entities", "evidence_source_ids"), duplicate_ids)
        for field in ("judge_negative_tests", "aggregate_negative_tests", "plumbing_negative_tests"):
            values = entities.get(field, ())
            if isinstance(values, list):
                _collect_duplicate_ids(
                    [item.get("id") for item in values if isinstance(item, Mapping)],
                    "traceability_ledger", ("entities", field), duplicate_ids,
                )
    source_files = snapshot.get("source_files", ())
    if isinstance(source_files, list):
        _collect_duplicate_ids([item.get("path") for item in source_files if isinstance(item, Mapping)],
                               "authority_snapshot", ("source_files",), duplicate_ids)
    if duplicate_ids:
        return _StageFailure(
            "authority_duplicate_identifier", tuple(duplicate_ids),
            replace(base, logical_arena_byte_count=arena.high_water),
        )

    # Stage 6 byte re-encoding.  Expectation object-key order alone is the
    # frozen grandfathered exception; its semantic arrays are checked later.
    canonical_failures: List[str] = []
    refs = inputs.refs()
    if not is_canonical_json(refs[0].buffer, snapshot_json):
        canonical_failures.append("/authority_snapshot")
    if not is_canonical_json(refs[1].buffer, reconciliation_json, indent=2):
        canonical_failures.append("/source_reconciliation")
    if not is_canonical_jsonl(_catalog_bytes(refs[3].buffer), catalog_parsed):
        canonical_failures.append("/obligation_catalog")
    if not is_canonical_json(refs[4].buffer, ledger_json, indent=2):
        canonical_failures.append("/traceability_ledger")
    if canonical_failures:
        return _StageFailure(
            "authority_order_violation", tuple(canonical_failures),
            replace(base, logical_arena_byte_count=arena.high_water),
        )

    return _Parsed(
        snapshot_json, reconciliation_json, expectation_json, catalog_parsed,
        ledger_json, snapshot, reconciliation, expectation, header, tuple(records),
        ledger, snapshot_value, ledger_value,
    )


def _collect_duplicate_ids(values: Iterable[Any], base: str, path: Sequence[Any], out: List[str]) -> None:
    seen: Set[Any] = set()
    for index, value in enumerate(values):
        if value in seen:
            out.append(_pointer(base, tuple(path) + (index,)))
        else:
            seen.add(value)


def _regular_failure(variant: str, pointers: Sequence[str], base: Accounting,
                     arena: RetainedArena) -> _StageFailure:
    return _StageFailure(
        variant, tuple(pointers), replace(base, logical_arena_byte_count=arena.high_water)
    )


def _validate_stages_7_28(parsed: _Parsed, base: Accounting,
                          arena: RetainedArena) -> Optional[_StageFailure]:
    snapshot = parsed.snapshot
    reconciliation = parsed.reconciliation
    expectation = parsed.expectation
    header = parsed.catalog_header
    records = parsed.catalog_records
    ledger = parsed.ledger

    path_fields: List[Tuple[str, Any]] = []
    for index, item in enumerate(snapshot.get("source_files", ())):
        path_fields.append(("/authority_snapshot/source_files/%d/path" % index, item.get("path")))
    for index, item in enumerate(snapshot.get("section_records", ())):
        path_fields.append(("/authority_snapshot/section_records/%d/path" % index, item.get("path")))
    for index, item in enumerate(snapshot.get("historical_objects", ())):
        path_fields.append(("/authority_snapshot/historical_objects/%d/path" % index, item.get("path")))
    for index, item in enumerate(records, 1):
        path_fields.append(("/obligation_catalog/%d/source_path" % index, item.get("source_path")))
    for index, item in enumerate(reconciliation.get("mappings", ())):
        path_fields.extend((
            ("/source_reconciliation/mappings/%d/old_path" % index, item.get("old_path")),
            ("/source_reconciliation/mappings/%d/current_path" % index, item.get("current_path")),
        ))
    absolute = [pointer for pointer, value in path_fields if isinstance(value, str) and value.startswith("/")]
    if absolute:
        return _regular_failure("authority_absolute_path", absolute, base, arena)
    traversal = [
        pointer for pointer, value in path_fields
        if isinstance(value, str) and (
            not value or "\\" in value or "//" in value or value.endswith("/")
            or any(part in ("", ".", "..") for part in value.split("/"))
        )
    ]
    if traversal:
        return _regular_failure("authority_path_traversal", traversal, base, arena)

    source_files = parsed.snapshot_value.source_files
    historical = parsed.snapshot_value.historical_objects
    symlinks = [
        "/authority_snapshot/source_files/%d/file_type" % index
        for index, item in enumerate(source_files) if item.file_type == "symlink"
    ] + [
        "/authority_snapshot/historical_objects/%d/git_object_type" % index
        for index, item in enumerate(historical) if item.git_object_type == "symlink"
    ]
    if symlinks:
        return _regular_failure("authority_symlink", symlinks, base, arena)
    wrong_types = [
        "/authority_snapshot/source_files/%d/file_type" % index
        for index, item in enumerate(source_files) if item.file_type not in ("regular", "symlink")
    ] + [
        "/authority_snapshot/historical_objects/%d/git_object_type" % index
        for index, item in enumerate(historical) if item.git_object_type not in ("blob", "symlink")
    ]
    if wrong_types:
        return _regular_failure("authority_non_regular_file", wrong_types, base, arena)

    files_by_path = {item.path: item for item in source_files}
    missing_sources: List[str] = []
    for ordinal, record in enumerate(records, 1):
        if record.get("source_path") not in files_by_path:
            missing_sources.append("/obligation_catalog/%d/source_path" % ordinal)
    if missing_sources:
        return _regular_failure("authority_missing_source", missing_sources, base, arena)

    expected_sides: List[Tuple[Any, Any, str]] = []
    for index, mapping in enumerate(reconciliation.get("mappings", ())):
        expected_sides.append((mapping.get("old_revision"), mapping.get("old_path"),
                               "/source_reconciliation/mappings/%d/old_path" % index))
        expected_sides.append((mapping.get("current_revision"), mapping.get("current_path"),
                               "/source_reconciliation/mappings/%d/current_path" % index))
    available = [(item.revision, item.path) for item in historical]
    if sorted((revision, path) for revision, path, _ in expected_sides) != sorted(available):
        pointers = [pointer for revision, path, pointer in expected_sides
                    if (revision, path) not in available]
        if not pointers:
            pointers = ["/authority_snapshot/historical_objects"]
        return _regular_failure("authority_missing_historical_object", pointers, base, arena)

    target = reconciliation.get("target_source", {})
    source_contract = expectation.get("source_contract", {})
    repositories = (snapshot.get("repository"), target.get("repository"),
                    source_contract.get("repository"))
    clean_required = (snapshot.get("clean_required"), target.get("clean_required"),
                      source_contract.get("clean_required"))
    if len(set(repositories)) != 1 or len(set(clean_required)) != 1:
        return _regular_failure("authority_repository_mismatch",
                                ("/authority_snapshot/repository",), base, arena)
    revision = snapshot.get("revision")
    relation = snapshot.get("revision_relation")
    head = snapshot.get("observed_head")
    ancestry = snapshot.get("ancestry_verified")
    matrix_ok = ((relation == "exact" and head == revision and ancestry is False)
                 or (relation == "descendant" and head != revision and ancestry is True))
    if (not matrix_ok or revision != target.get("revision")
            or revision != source_contract.get("revision")):
        return _regular_failure("authority_revision_mismatch",
                                ("/authority_snapshot/revision",), base, arena)
    if snapshot.get("clean") is False:
        return _regular_failure("authority_dirty_repository",
                                ("/authority_snapshot/clean",), base, arena)

    digest_mismatches: List[str] = []
    for ordinal, record in enumerate(records, 1):
        item = files_by_path.get(record.get("source_path"))
        if item is not None and item.sha256 != record.get("source_file_sha256"):
            digest_mismatches.append("/obligation_catalog/%d/source_file_sha256" % ordinal)
    hist_by_key = {(item.revision, item.path): item for item in historical}
    for index, mapping in enumerate(reconciliation.get("mappings", ())):
        for side in ("old", "current"):
            item = hist_by_key.get((mapping.get(side + "_revision"), mapping.get(side + "_path")))
            if item is not None and item.sha256 != mapping.get(side + "_sha256"):
                digest_mismatches.append("/source_reconciliation/mappings/%d/%s_sha256" % (index, side))
    authority = expectation.get("authority", {})
    reconciliation_digest = hashlib.sha256(
        canonical_json_bytes(parsed.reconciliation_json, indent=2)
    ).hexdigest()
    if authority.get("source_reconciliation_sha256") != reconciliation_digest:
        digest_mismatches.append("/benchmark_expectation/authority/source_reconciliation_sha256")
    if digest_mismatches:
        return _regular_failure("authority_file_digest_mismatch", digest_mismatches, base, arena)

    section_by_range: Dict[Tuple[Any, Any, Any], Any] = {}
    section_failures: List[str] = []
    for index, item in enumerate(parsed.snapshot_value.section_records):
        key = (item.path, item.start, item.end)
        if key in section_by_range:
            section_failures.append("/authority_snapshot/section_records/%d" % index)
        section_by_range[key] = item
    expected_ranges: Set[Tuple[Any, Any, Any]] = set()
    for ordinal, record in enumerate(records, 1):
        key = (record.get("source_path"), record.get("section_start"), record.get("section_end"))
        expected_ranges.add(key)
        file_record = files_by_path.get(key[0])
        if (not isinstance(key[1], int) or isinstance(key[1], bool)
                or not isinstance(key[2], int) or isinstance(key[2], bool)
                or key[1] >= key[2] or (file_record is not None and key[2] > file_record.byte_count)):
            section_failures.append("/obligation_catalog/%d/section_end" % ordinal)
    if set(section_by_range) != expected_ranges:
        section_failures.append("/authority_snapshot/section_records")
    if section_failures:
        return _regular_failure("authority_invalid_section_range", section_failures, base, arena)
    section_digest_failures: List[str] = []
    for ordinal, record in enumerate(records, 1):
        item = section_by_range.get((record.get("source_path"), record.get("section_start"),
                                     record.get("section_end")))
        if item is not None and item.sha256 != record.get("section_sha256"):
            section_digest_failures.append("/obligation_catalog/%d/section_sha256" % ordinal)
    if section_digest_failures:
        return _regular_failure("authority_section_digest_mismatch",
                                section_digest_failures, base, arena)

    descriptor = parsed.snapshot_value.repository_control
    if (descriptor.observed_head != snapshot.get("observed_head")
            or descriptor.clean != snapshot.get("clean")
            or descriptor.qualified_preflight is not True):
        return _regular_failure("authority_repository_control_unsupported",
                                ("/authority_snapshot/repository_control",), base, arena)
    for index, item in enumerate(source_files):
        if item.no_follow_identity.size != item.byte_count:
            return _regular_failure("authority_repository_control_unsupported",
                                    ("/authority_snapshot/source_files/%d/no_follow_identity/size" % index,),
                                    base, arena)

    binds = parsed.ledger_value.binds
    catalog_digest = hashlib.sha256(
        b"".join(canonical_json_bytes(item) for item in parsed.catalog_json)
    ).hexdigest()
    if binds.catalog_sha256 != catalog_digest:
        return _regular_failure("catalog_digest_mismatch",
                                ("/traceability_ledger/binds/catalog_sha256",), base, arena)
    if (header.get("design_gate_revision") != parsed.ledger_value.design_gate_revision
            or any(record.get("design_gate_revision") != header.get("design_gate_revision")
                   for record in records)):
        return _regular_failure("catalog_gate_revision_mismatch",
                                ("/obligation_catalog/0/design_gate_revision",), base, arena)
    # Stages 22 and 27/28 also depend on the consumed payload and are completed
    # by the caller immediately after this pure input-only sweep.
    if header.get("predecessor") != ledger.get("predecessor"):
        return _regular_failure("catalog_predecessor_mismatch",
                                ("/obligation_catalog/0/predecessor",), base, arena)
    predecessor = header.get("predecessor")
    if isinstance(predecessor, Mapping) and predecessor.get("compile_custody_lineage_id") == header.get("compile_custody_lineage_id"):
        return _regular_failure("catalog_predecessor_mismatch",
                                ("/obligation_catalog/0/compile_custody_lineage_id",), base, arena)
    if len(records) + 1 > 512 or header.get("record_count") != len(records) + 1:
        return _regular_failure("catalog_record_limit_exceeded",
                                ("/obligation_catalog/0/record_count",), base, arena)
    cited = {record.get("source_path") for record in records}
    orphan_sources = ["/authority_snapshot/source_files/%d/path" % index
                      for index, item in enumerate(source_files) if item.path not in cited]
    if orphan_sources:
        return _regular_failure("catalog_orphan_source", orphan_sources, base, arena)
    for ordinal, record in enumerate(records, 1):
        status = record.get("readiness_status")
        blockers = record.get("blocker_ids")
        if not isinstance(status, str) or not isinstance(blockers, list):
            return _regular_failure("catalog_invalid_status",
                                    ("/obligation_catalog/%d/readiness_status" % ordinal,), base, arena)
    return None


def _admission_resource_failure(parsed: _Parsed, base: Accounting,
                                limits: _CompileLimits) -> Optional[_StageFailure]:
    file_count = 0
    byte_count = 0
    for item in parsed.snapshot_value.source_files:
        file_count += 1
        byte_count += item.byte_count
        pointers = []
        if file_count > limits.authority_file_count_max:
            pointers.append("/resources/authority_source/file_count")
        if item.byte_count > limits.authority_file_bytes_max:
            pointers.append("/resources/authority_source/per_file_bytes")
        if byte_count > limits.authority_total_bytes_max:
            pointers.append("/resources/authority_source/total_bytes")
        if pointers:
            observed = replace(base, authority_file_count=file_count,
                               authority_byte_count=byte_count)
            return _StageFailure("resource_source_cap_exceeded", tuple(pointers), observed)
    section_count = 0
    section_bytes = 0
    for record in parsed.catalog_records:
        section_count += 1
        start = record.get("section_start")
        end = record.get("section_end")
        if isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool) and end >= start:
            section_bytes += end - start
        pointers = []
        if section_count > limits.authority_section_count_max:
            pointers.append("/resources/authority_section/count")
        if section_bytes > limits.authority_section_bytes_total_max:
            pointers.append("/resources/authority_section/total_bytes")
        if pointers:
            observed = replace(
                base, authority_file_count=file_count, authority_byte_count=byte_count,
                section_count=section_count, section_byte_count=section_bytes,
                catalog_record_count=section_count + 1,
            )
            return _StageFailure("resource_section_cap_exceeded", tuple(pointers), observed)
    return None


def _iter_json_values(value: Any) -> Iterable[Any]:
    stack = [value]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, Mapping):
            stack.extend(reversed(tuple(current.values())))
        elif isinstance(current, (list, tuple)):
            stack.extend(reversed(current))


def _charge_parsed(values: Sequence[Any], arena: RetainedArena, fault: Any,
                   base: Accounting) -> Tuple[Optional[_StageFailure], Set[bytes]]:
    interned: Set[bytes] = set()
    for root in values:
        for value in _iter_json_values(root):
            charges: List[Tuple[int, str]] = []
            if isinstance(value, Mapping):
                charges.append((8 * len(value), "parsed-object"))
            elif isinstance(value, (list, tuple)):
                charges.append((8 * len(value), "parsed-array"))
            elif isinstance(value, str):
                encoded = value.encode("utf-8")
                if _SHA256_RE.fullmatch(value):
                    charges.append((32, "parsed-digest"))
                elif _IDENTIFIER_RE.fullmatch(value):
                    if encoded not in interned:
                        charges.append((16 + len(encoded), "input-identifier-intern"))
                    charges.append((8, "parsed-identifier"))
                else:
                    charges.append((len(encoded), "parsed-string"))
            elif value is None:
                charges.append((8, "parsed-scalar"))
            elif isinstance(value, (bool, int)):
                charges.append((8, "parsed-scalar"))
            for amount, label in charges:
                before = arena.used
                try:
                    marker = arena.modeled_allocate(amount, label, object, fault)
                except MemoryError:
                    accounting = replace(base, logical_arena_byte_count=before)
                    return _StageFailure("resource_allocation_failed",
                                         ("/resources/allocation",), accounting), interned
                if marker is None:
                    accounting = replace(base, logical_arena_byte_count=arena.last_candidate)
                    return _StageFailure("resource_index_cap_exceeded",
                                         ("/resources/retained_index_arena",), accounting), interned
                if label == "input-identifier-intern":
                    interned.add(value.encode("utf-8"))
    return None, interned


def _case_ids(expectation: Mapping[str, Any]) -> Tuple[str, ...]:
    values: List[str] = []
    for lane in expectation.get("lanes", ()):
        lane_id = lane.get("id")
        ordinal = 1
        for matrix in lane.get("matrices", ()):
            count = 1
            for dimension in matrix.get("dimensions", ()):
                count *= len(dimension.get("values", ()))
            for _unused in range(count):
                values.append("%s.%s.%04d" % (lane_id, matrix.get("id"), ordinal))
                ordinal += 1
    return tuple(values)


def _source_commitments(parsed: _Parsed) -> Tuple[Dict[str, str], Dict[str, Any]]:
    snapshot = parsed.snapshot
    descriptor = snapshot["repository_control"]
    streams = (
        ("repository_control_sha256", (descriptor,)),
        ("source_files_sha256", tuple(snapshot["source_files"])),
        ("section_records_sha256", tuple(snapshot["section_records"])),
        ("historical_objects_sha256", tuple(snapshot["historical_objects"])),
    )
    digests: Dict[str, str] = {}
    for name, records in streams:
        state = hashlib.sha256()
        for record in records:
            state.update(canonical_json_bytes(dict(record)))
        digests[name] = state.hexdigest()
    source = {
        "repository": snapshot["repository"],
        "revision": snapshot["revision"],
        "observed_head": snapshot["observed_head"],
        "revision_relation": snapshot["revision_relation"],
        "clean_required": snapshot["clean_required"],
        "clean": snapshot["clean"],
        "repository_control_version": descriptor["schema_version"],
        "source_files": [
            {key: record[key] for key in ("path", "byte_count", "sha256", "file_type")}
            for record in snapshot["source_files"]
        ],
        "section_records": [dict(record) for record in snapshot["section_records"]],
        "historical_objects": [dict(record) for record in snapshot["historical_objects"]],
    }
    source.update(digests)
    return digests, source


def _complete_payload_checks(parsed: _Parsed, payload: Any, digests: Tuple[str, ...],
                             base: Accounting, arena: RetainedArena) -> Optional[_StageFailure]:
    header = parsed.catalog_header
    lineage_fields = (
        ("compile_custody_lineage_id", payload.custody_lineage_id),
        ("custody_domain_id", payload.custody_domain_id),
        ("custody_domain_sha256", payload.custody_domain_sha256),
        ("t_max", payload.t_max),
        ("compile_custody_policy_sha256", payload.compile_custody_policy_sha256),
    )
    mismatch = ["/obligation_catalog/0/%s" % name for name, expected in lineage_fields
                if header.get(name) != expected]
    if mismatch:
        return _regular_failure("catalog_lineage_mismatch", mismatch, base, arena)
    if (header.get("expectation_sha256") != digests[2]
            or parsed.ledger_value.binds.expectation_sha256 != digests[2]):
        return _regular_failure("expectation_digest_mismatch",
                                ("/obligation_catalog/0/expectation_sha256",), base, arena)
    remaining = (
        (parsed.ledger_value.binds.source_reconciliation_sha256, digests[1],
         "/traceability_ledger/binds/source_reconciliation_sha256"),
        (parsed.ledger_value.binds.compile_custody_policy_sha256,
         payload.compile_custody_policy_sha256,
         "/traceability_ledger/binds/compile_custody_policy_sha256"),
        (parsed.ledger_value.binds.custody_domain_sha256, payload.custody_domain_sha256,
         "/traceability_ledger/binds/custody_domain_sha256"),
    )
    pointers = [pointer for actual, expected, pointer in remaining if actual != expected]
    if pointers:
        return _regular_failure("traceability_digest_mismatch", pointers, base, arena)
    return None


def _plan_counts(validated: PhaseBResult, parsed: _Parsed, base: Accounting,
                 arena_high_water: int) -> Dict[str, int]:
    h = validated.h
    m = validated.m
    r_he = validated.r_he
    q_path = 4 * h + r_he + 211
    r_path = 11 * h + 2 * r_he + 422
    q_total = q_path + m + 104
    r_total = r_path + 2 * m + 208
    return {
        "lane_count": 12, "behavior_case_count": 46, "case_plan_count": 425,
        "obligation_count": 46, "judge_count": 46, "evidence_bundle_count": 12,
        "evidence_source_count": 41, "lane_context_contract_count": 2,
        "post_gate_output_contract_count": 2, "gate_count": 58,
        "judge_negative_template_count": 46, "aggregate_negative_template_count": 58,
        "plumbing_negative_template_count": 58, "obligation_gate_pair_count": m,
        "enforcement_path_count": h, "bundle_evidence_record_count": 52,
        "path_case_record_count": h, "path_gate_record_count": h,
        "path_evidence_record_count": r_he, "path_judge_negative_record_count": h,
        "gate_aggregate_negative_record_count": 58,
        "gate_plumbing_negative_record_count": 58,
        "lane_context_record_count": 21, "lane_output_record_count": 22,
        "artifact_membership_total": 95, "relation_path_record_count": q_path,
        "relation_support_record_count": m + 104,
        "relation_total_record_count": q_total,
        "endpoint_path_reference_count": r_path,
        "endpoint_support_reference_count": 2 * m + 208,
        "endpoint_total_reference_count": r_total,
        "entity_record_count": h + 852,
        "relation_only_record_count": 3 * h + r_he + m + 315,
        "serialized_input_byte_count": base.serialized_input_byte_count,
        "authority_file_count": len(parsed.snapshot_value.source_files),
        "authority_byte_count": sum(item.byte_count for item in parsed.snapshot_value.source_files),
        "section_count": len(parsed.catalog_records),
        "section_byte_count": sum(record["section_end"] - record["section_start"]
                                  for record in parsed.catalog_records),
        "logical_arena_byte_count": arena_high_water,
        "key_count_exec": validated.k_exec,
        "key_count_run": validated.k_exec + 22,
    }


def _run_pipeline(payload: Any, AuthoritySnapshot: Any, ObligationCatalog: Any,
                  BenchmarkExpectation: Any, TraceabilityLedger: Any,
                  CompileLimits: Any, fault: Any = None) -> _RESULT:
    if not isinstance(CompileLimits, _CompileLimits) or not CompileLimits.is_frozen():
        raise CompilerPreconditionViolation("input_identity_mismatch")
    if _compile_limits_sha256(CompileLimits) != payload.compile_limits_sha256:
        raise CompilerPreconditionViolation("input_identity_mismatch")
    inputs = CompilerInputs.from_envelopes(
        AuthoritySnapshot, ObligationCatalog, BenchmarkExpectation, TraceabilityLedger
    )

    lengths = inputs.nbytes()
    expected_lengths = (
        payload.authority_snapshot_byte_count,
        payload.source_reconciliation_byte_count,
        payload.expectation_byte_count,
        payload.catalog_byte_count,
        payload.traceability_byte_count,
    )
    if lengths != expected_lengths:
        raise CompilerPreconditionViolation("input_identity_mismatch")
    try:
        total = 0
        for length in lengths:
            total = checked_add(total, length, "serialized input total")
    except ContractError:
        zero = Accounting()
        return _materialize_failure(
            payload, _StageFailure("resource_checked_arithmetic_overflow",
                                   ("/resources/checked_arithmetic",), zero)
        )
    k0 = Accounting(serialized_input_byte_count=total)
    cap_pointers = [
        "/resources/serialized_input/%s" % name
        for (name, _ref), length in zip(inputs.iter_named(), lengths)
        if length > CompileLimits.largest_single_serialized_parser_input_max
    ]
    if total > CompileLimits.serialized_input_bytes_total_max:
        cap_pointers.append("/resources/serialized_input/total")
    if cap_pointers:
        return _materialize_failure(
            payload, _StageFailure("resource_input_cap_exceeded", tuple(cap_pointers), k0)
        )

    arena = RetainedArena(CompileLimits.logical_retained_index_arena_max)
    observed_digests: List[str] = []
    for ref in inputs.refs():
        before = arena.used
        try:
            marker = arena.retain_s8_hash_state(fault)
        except MemoryError:
            return _materialize_failure(
                payload, _StageFailure("resource_allocation_failed",
                                       ("/resources/allocation",),
                                       replace(k0, logical_arena_byte_count=before))
            )
        if marker is None:
            return _materialize_failure(
                payload, _StageFailure("resource_index_cap_exceeded",
                                       ("/resources/retained_index_arena",),
                                       replace(k0, logical_arena_byte_count=arena.last_candidate))
            )
        observed_digests.append(sha256_bound_bytes(ref))
        arena.release(SHA256_STATE_CHARGE)
    digests = tuple(observed_digests)
    expected_digests = (
        payload.authority_snapshot_sha256, payload.source_reconciliation_sha256,
        payload.expectation_sha256, payload.catalog_sha256,
        payload.traceability_sha256,
    )
    if digests != expected_digests or compute_input_set_sha256(payload) != payload.input_set_sha256:
        raise CompilerPreconditionViolation("input_identity_mismatch")

    # Phase A begins with the one physical W_B slab.
    before = arena.used
    try:
        scratch = PhaseBScratch.allocate(arena, fault)
    except MemoryError:
        return _materialize_failure(
            payload, _StageFailure("resource_allocation_failed", ("/resources/allocation",),
                                   replace(k0, logical_arena_byte_count=before))
        )
    if scratch is None:
        return _materialize_failure(
            payload, _StageFailure("resource_index_cap_exceeded",
                                   ("/resources/retained_index_arena",),
                                   replace(k0, logical_arena_byte_count=arena.last_candidate))
        )

    parsed_or_failure = _parse_phase_a(inputs, k0, arena)
    if isinstance(parsed_or_failure, _StageFailure):
        return _materialize_failure(payload, parsed_or_failure)
    parsed = parsed_or_failure

    structural = replace(
        k0,
        authority_file_count=len(parsed.snapshot_value.source_files),
        authority_byte_count=sum(item.byte_count for item in parsed.snapshot_value.source_files),
        section_count=len(parsed.catalog_records),
        section_byte_count=sum(record["section_end"] - record["section_start"]
                               for record in parsed.catalog_records),
        catalog_record_count=1 + len(parsed.catalog_records),
    )

    admission_failure = _admission_resource_failure(parsed, k0, CompileLimits)
    if admission_failure is not None:
        return _materialize_failure(payload, admission_failure)

    charge_failure, interned = _charge_parsed(
        (parsed.snapshot, parsed.reconciliation, parsed.expectation,
         (parsed.catalog_header,) + parsed.catalog_records, parsed.ledger),
        arena, fault, structural,
    )
    if charge_failure is not None:
        return _materialize_failure(payload, charge_failure)
    for case_id in _case_ids(parsed.expectation):
        before = arena.used
        try:
            admitted, _created = arena.visit_case_id(case_id, interned, fault)
        except MemoryError:
            return _materialize_failure(
                payload, _StageFailure("resource_allocation_failed", ("/resources/allocation",),
                                       replace(structural, logical_arena_byte_count=before))
            )
        if not admitted:
            return _materialize_failure(
                payload, _StageFailure("resource_index_cap_exceeded",
                                       ("/resources/retained_index_arena",),
                                       replace(structural,
                                               logical_arena_byte_count=arena.last_candidate))
            )

    semantic_failure = _validate_stages_7_28(parsed, structural, arena)
    if semantic_failure is not None:
        return _materialize_failure(payload, semantic_failure)
    payload_failure = _complete_payload_checks(parsed, payload, digests, structural, arena)
    if payload_failure is not None:
        return _materialize_failure(payload, payload_failure)

    # End-of-Phase-A source commitments: one physical h_max allocation, then
    # logical release into its private reusable slot.
    before = arena.used
    try:
        shared_hash = SharedHashBuffer.allocate_phase_a(arena, fault)
    except MemoryError:
        return _materialize_failure(
            payload, _StageFailure("resource_allocation_failed", ("/resources/allocation",),
                                   replace(structural, logical_arena_byte_count=before))
        )
    if shared_hash is None:
        return _materialize_failure(
            payload, _StageFailure("resource_index_cap_exceeded",
                                   ("/resources/retained_index_arena",),
                                   replace(structural,
                                           logical_arena_byte_count=arena.last_candidate))
        )
    _source_digests, source = _source_commitments(parsed)
    shared_hash.logical_release(arena)
    parse_retained = arena.used - W_B

    # Phase B: one validation-only sweep.  Its structures are fixed-offset
    # regions in W_B and therefore create no modeled allocation ordinal.
    scratch.clear_counter_block()
    m_hint = len(parsed.ledger_value.entities.obligation_gate_pairs)
    k2 = replace(
        structural,
        entity_count=852,
        relation_record_count=199 + m_hint,
        endpoint_reference_count=398 + 2 * m_hint,
        path_count=0,
        logical_arena_byte_count=parse_retained + W_B,
    )
    catalog_protocol = {"header": parsed.catalog_header,
                        "obligations": parsed.catalog_records,
                        "records": parsed.catalog_records}
    try:
        validated = validate_relations(
            catalog_protocol, parsed.expectation, parsed.ledger_value, CompileLimits
        )
    except RelationValidationError as error:
        scratch.release_region()
        return _materialize_failure(
            payload, _StageFailure(error.variant, error.pointers, k2)
        )
    except ContractError:
        scratch.release_region()
        return _materialize_failure(
            payload, _StageFailure("resource_checked_arithmetic_overflow",
                                   ("/resources/checked_arithmetic",), k2)
        )
    scratch.release_region()

    # Phase C: release W_B before the first derived retention.  Hook updates
    # partial K3 counters only after a modeled allocation succeeds.
    arena.release(W_B)
    counters = _DerivedCounters()

    def retain_derived(label: str, charge: int) -> None:
        before_used = arena.used
        before_accounting = counters.accounting(structural, validated.m, before_used)
        try:
            marker = arena.modeled_allocate(charge, "phase-c-" + label, object, fault)
        except MemoryError:
            raise StopIteration(_StageFailure(
                "resource_allocation_failed", ("/resources/allocation",), before_accounting
            ))
        if marker is None:
            raise StopIteration(_StageFailure(
                "resource_index_cap_exceeded", ("/resources/retained_index_arena",),
                replace(before_accounting,
                        logical_arena_byte_count=arena.last_candidate),
            ))
        counters.admitted(label)

    try:
        derived = derive_relations(validated, retain_derived)
    except StopIteration as abort:
        if len(abort.args) == 1 and isinstance(abort.args[0], _StageFailure):
            return _materialize_failure(payload, abort.args[0])
        raise CompilerInternalError("invalid Phase C abort") from abort

    q_total = 4 * validated.h + validated.r_he + validated.m + 315
    r_total = 11 * validated.h + 2 * validated.r_he + 2 * validated.m + 630
    k4 = replace(
        structural,
        entity_count=validated.h + 852,
        relation_record_count=q_total,
        endpoint_reference_count=r_total,
        path_count=validated.h,
        logical_arena_byte_count=arena.used,
    )

    # Phase D: the h_max storage is reused (logical reserve only), while each
    # of the 27 digest states is one modeled allocation held for its stream.
    if not shared_hash.logical_reserve_phase_d(arena):
        return _materialize_failure(
            payload, _StageFailure("resource_index_cap_exceeded",
                                   ("/resources/retained_index_arena",),
                                   replace(k4, logical_arena_byte_count=arena.last_candidate))
        )
    live_digest_state = [False]

    def retain_digest_state(label: str, charge: int) -> None:
        if charge != SHA256_STATE_CHARGE:
            raise CompilerInternalError("invalid Phase D digest-state charge")
        if live_digest_state[0]:
            arena.release(SHA256_STATE_CHARGE)
            live_digest_state[0] = False
        before_used = arena.used
        try:
            marker = arena.modeled_allocate(charge, "phase-d-digest-" + label, object, fault)
        except MemoryError:
            raise StopIteration(_StageFailure(
                "resource_allocation_failed", ("/resources/allocation",),
                replace(k4, logical_arena_byte_count=before_used),
            ))
        if marker is None:
            raise StopIteration(_StageFailure(
                "resource_index_cap_exceeded", ("/resources/retained_index_arena",),
                replace(k4, logical_arena_byte_count=arena.last_candidate),
            ))
        live_digest_state[0] = True

    try:
        digest_streams = digest_relations(validated, derived, retain_digest_state)
    except StopIteration as abort:
        if len(abort.args) == 1 and isinstance(abort.args[0], _StageFailure):
            return _materialize_failure(payload, abort.args[0])
        raise CompilerInternalError("invalid Phase D abort") from abort
    finally:
        if live_digest_state[0]:
            arena.release(SHA256_STATE_CHARGE)
            live_digest_state[0] = False
    shared_hash.logical_release(arena)

    # Phase E enumerates and k-way-merges the frozen key streams.
    try:
        expected = enumerate_expected_keys(validated, derived, payload.t_max)
    except RelationValidationError as error:
        return _materialize_failure(
            payload, _StageFailure(error.variant, error.pointers, k4)
        )
    except ValueError as error:
        raise CompilerInternalError("private key encoder/decoder invariant failed") from error

    plan_inputs = {
        "source_reconciliation_path": AUTHORITY_PATHS["source_reconciliation_path"],
        "source_reconciliation_sha256": payload.source_reconciliation_sha256,
        "expectation_path": AUTHORITY_PATHS["expectation_path"],
        "expectation_sha256": payload.expectation_sha256,
        "catalog_path": AUTHORITY_PATHS["catalog_path"],
        "catalog_sha256": payload.catalog_sha256,
        "traceability_path": AUTHORITY_PATHS["traceability_path"],
        "traceability_sha256": payload.traceability_sha256,
        "authority_snapshot_sha256": payload.authority_snapshot_sha256,
        "compile_limits_sha256": payload.compile_limits_sha256,
        "traceability_bindings": _plain_dataclass(parsed.ledger_value.binds),
    }
    counts = _plan_counts(validated, parsed, structural, arena.high_water)
    expected_object = {
        "names": expected.names,
        "count": expected.count,
        "sha256": expected.sha256,
        "compile_key_count": expected.compile_key_count,
        "execution_key_count": expected.execution_key_count,
        "run_global_key_count": expected.run_global_key_count,
    }
    plan_fields = _prefix(payload)
    plan_fields.update(
        inputs=plan_inputs,
        source=source,
        entity_sets=digest_streams.entity_sha256,
        relation_sets=digest_streams.relation_sha256,
        counts=counts,
        limits=CompileLimits,
        expected_keys=expected_object,
        limitations=LIMITATIONS,
    )
    try:
        plan = build_coverage_plan(**plan_fields)
    except ContractError as error:
        raise CompilerInternalError("CoveragePlan builder rejected validated pipeline state") from error

    # Stage 47 precedes the sole final-output modeled allocation.
    candidate = plan.byte_count
    if candidate > CompileLimits.coverage_plan_or_failure_max:
        return _materialize_failure(
            payload, _StageFailure("resource_output_cap_exceeded",
                                   ("/resources/output_bytes",),
                                   replace(k4, output_byte_count_attempted=candidate))
        )
    try:
        sink = OutputSink(candidate, CompileLimits, fault)
    except MemoryError:
        return _materialize_failure(
            payload, _StageFailure("resource_allocation_failed", ("/resources/allocation",), k4)
        )
    offset = 0
    while offset < candidate:
        end = min(candidate, offset + CompileLimits.output_streaming_chunk_max)
        sink.write(plan.canonical_bytes[offset:end])
        offset = end
    if sink.finish() != plan.canonical_bytes:
        raise CompilerInternalError("output builder diverged from canonical plan bytes")
    return plan


def _plain_dataclass(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {name: _plain_dataclass(getattr(value, name))
                for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {key: _plain_dataclass(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_dataclass(child) for child in value]
    return value


def compile(CompilePermit, AuthoritySnapshot, ObligationCatalog,
            BenchmarkExpectation, TraceabilityLedger, CompileLimits) -> _RESULT:
    """Consume one production permit and execute the frozen compiler once."""
    if not isinstance(CompilePermit, _CompilePermit):
        raise CompilerInternalError("compile received a non-CompilePermit capability")
    payload = CompilePermit.consume()
    CompilePermit._check_issuing_process()
    CompilePermit._check_production_entry()
    return _run_pipeline(
        payload, AuthoritySnapshot, ObligationCatalog, BenchmarkExpectation,
        TraceabilityLedger, CompileLimits,
    )


def _compile_test(CompilePermit, AuthoritySnapshot, ObligationCatalog,
                  BenchmarkExpectation, TraceabilityLedger, CompileLimits) -> _RESULT:
    """Tests-only mirror entry; a production permit is an internal invariant."""
    if not isinstance(CompilePermit, _CompilePermit):
        raise CompilerInternalError("test compiler received a non-CompilePermit capability")
    payload = CompilePermit.consume()
    CompilePermit._check_issuing_process()
    CompilePermit._check_test_entry()
    # The tests-only issuer may attach the accounting module's private injector
    # to the permit.  Production compile() never reads or forwards this seam.
    fault = getattr(CompilePermit, "_fault_injector", None)
    return _run_pipeline(
        payload, AuthoritySnapshot, ObligationCatalog, BenchmarkExpectation,
        TraceabilityLedger, CompileLimits, fault,
    )
