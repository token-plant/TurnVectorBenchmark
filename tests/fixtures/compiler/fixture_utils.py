"""Deterministic synthetic inputs for CoverageCompiler tests.

The fixture is generated from the checked-in expectation and reconciliation so
it cannot silently drift away from their lane, case, gate, or revision sets.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = FIXTURE_ROOT / "source"
EXPECTATION_PATH = ROOT / "expectations" / "turnvector-implementation-v2.json"
RECONCILIATION_PATH = ROOT / "authority" / "source-reconciliation-v1.json"

LANE_OBLIGATION_COUNTS = [4, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4]
LANE_CASE_COUNTS = [20, 3, 40, 15, 18, 36, 80, 36, 36, 22, 24, 95]
LANE_GATE_COUNTS = [3, 3, 4, 5, 4, 4, 7, 7, 7, 4, 5, 5]
LANE_RAW_COUNTS = [3, 1, 4, 4, 5, 4, 5, 5, 6, 7, 4, 4]
LANE_CONTEXT_COUNTS = [1, 2, 2, 1, 2, 2, 2, 2, 2, 1, 2, 2]
LANE_OUTPUT_COUNTS = [1, 2, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2]
DESIGN_REVISION = "ab" * 32
POLICY_SHA256 = "12" * 32
CUSTODY_SHA256 = "34" * 32
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FIRST_CASE_ID = "core-event-replay.effect-result-replay.0001"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic_digest(label: str) -> str:
    return digest(("fixture:" + label).encode("ascii"))


def compact(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def pretty(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode(
        "ascii"
    )


def jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(compact(record) for record in records)


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def case_ids(expectation: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for lane in expectation["lanes"]:
        ordinal = 0
        for matrix in lane["matrices"]:
            dimensions = [dimension["values"] for dimension in matrix["dimensions"]]
            for _ in product(*dimensions):
                ordinal += 1
                result.append(f"{lane['id']}.{matrix['id']}.{ordinal:04d}")
    return result


def long_identifier(domain: str, ordinal: int, fill: int) -> str:
    value = f"fixture-{domain}-{ordinal:04d}" + "x" * fill
    assert len(value.encode("ascii")) == 1024
    assert IDENTIFIER.fullmatch(value)
    return value


def long_gate_id(ordinal: int) -> str:
    return long_identifier("gate", ordinal, 1007)


def expectation_variant(expectation: dict[str, Any]) -> dict[str, Any]:
    variant = copy.deepcopy(expectation)
    ordinal = 0
    for lane in variant["lanes"]:
        for gate in lane["gates"]:
            ordinal += 1
            gate["id"] = long_gate_id(ordinal)
    assert ordinal == 58
    return variant


def expectation_bytes(value: dict[str, Any], *, live: bool) -> bytes:
    if live:
        raw = EXPECTATION_PATH.read_bytes()
        assert json.loads(raw) == value
        return raw
    # Preserve the live document's grandfathered declaration-key layout.
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _identity(size: int, ordinal: int) -> dict[str, int]:
    return {
        "device": 1,
        "inode": ordinal,
        "uid": 501,
        "gid": 20,
        "mode": 33188,
        "size": size,
        "mtime_ns": 1_000_000 + ordinal,
        "ctime_ns": 2_000_000 + ordinal,
    }


def _directory_identity(ordinal: int) -> dict[str, int]:
    value = _identity(0, ordinal)
    value["mode"] = 16877
    return value


def _root(namespace: str, path: str, ordinal: int) -> dict[str, Any]:
    return {"kind": "root", "namespace": namespace, "absolute_path": path, **_directory_identity(ordinal)}


def _control(namespace: str, path: str, data: bytes, ordinal: int) -> dict[str, Any]:
    return {
        "kind": "control", "namespace": namespace, "path": path,
        "presence": "present", "type": "regular", "byte_count": len(data),
        "sha256": digest(data), "no_follow_identity": _identity(len(data), ordinal),
        "directory_identity": None,
    }


def _directory(namespace: str, path: str, ordinal: int) -> dict[str, Any]:
    return {
        "kind": "control", "namespace": namespace, "path": path,
        "presence": "present", "type": "directory", "byte_count": 0,
        "sha256": EMPTY_SHA256, "no_follow_identity": None,
        "directory_identity": _directory_identity(ordinal),
    }


def _record_digest(record: dict[str, Any]) -> str:
    return digest(compact(record))


def _witness(namespace: str, requested: str, parent: str, missing: str,
             suffix: list[str], parent_record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    locator = {"namespace": namespace, "requested_path": requested}
    key = digest(compact(locator))
    return key, {
        **locator,
        "closest_existing_parent_path": parent,
        "parent_stability_record_sha256": _record_digest(parent_record),
        "first_missing_component": missing,
        "unresolved_suffix": suffix,
    }


def _stream_digest(records: list[dict[str, Any]]) -> str:
    return digest(b"".join(compact(record) for record in records))


def repository_control(observed_head: str, *, worktree_kind: str = "linked") -> dict[str, Any]:
    roots = [
        _root("worktree", "/fixture/worktree", 1),
        _root("git_dir", "/fixture/git-dir", 2),
        _root("common_dir", "/fixture/common-dir", 3),
    ]
    controls = [
        _control("git_dir", "HEAD", b"ref: refs/heads/fixture\n", 10),
        _control("git_dir", "commondir", b"../..\n", 11),
        _control("git_dir", "gitdir", b"/fixture/worktree/.git\n", 12),
        _control("git_dir", "index", b"fixture-index\n", 13),
        _control("common_dir", "config", b"[core]\nrepositoryformatversion = 0\nbare = false\n", 14),
        _control("common_dir", "info/exclude", b"# fixture\n", 15),
        _directory("common_dir", "info", 16),
        _directory("common_dir", "objects", 17),
        _directory("common_dir", "objects/info", 18),
        _directory("common_dir", "refs", 19),
    ]
    by_key = {(r["namespace"], r.get("path", "")): r for r in roots + controls}
    witness_specs = [
        ("git_dir", "config.worktree", "", "config.worktree", []),
        ("common_dir", "shallow", "", "shallow", []),
        ("common_dir", "packed-refs", "", "packed-refs", []),
        ("git_dir", "index.lock", "", "index.lock", []),
        ("git_dir", "HEAD.lock", "", "HEAD.lock", []),
        ("common_dir", "config.lock", "", "config.lock", []),
        ("common_dir", "packed-refs.lock", "", "packed-refs.lock", []),
        ("common_dir", "shallow.lock", "", "shallow.lock", []),
        ("common_dir", "objects/info/alternates", "objects/info", "alternates", []),
        ("common_dir", "info/grafts", "info", "grafts", []),
        ("common_dir", "info/attributes", "info", "attributes", []),
        ("git_dir", "info/attributes", "", "info", ["attributes"]),
        ("common_dir", "refs/replace", "refs", "replace", []),
    ]
    witnesses: dict[str, Any] = {}
    witness_for: dict[tuple[str, str], str] = {}
    for ns, requested, parent, missing, suffix in witness_specs:
        key, value = _witness(ns, requested, parent, missing, suffix, by_key[(ns, parent)])
        witnesses[key] = value
        witness_for[(ns, requested)] = key

    present_files = [record for record in controls if record["type"] == "regular"]
    control_files = [
        {"namespace": r["namespace"], "path": r["path"], "presence": "present",
         "byte_count": r["byte_count"], "sha256": r["sha256"],
         "no_follow_identity": r["no_follow_identity"], "witness_ref": None}
        for r in present_files
    ]
    for ns, path in [("git_dir", "config.worktree"), ("common_dir", "shallow"),
                     ("common_dir", "packed-refs")]:
        control_files.append({"namespace": ns, "path": path, "presence": "absent",
                              "byte_count": 0, "sha256": EMPTY_SHA256,
                              "no_follow_identity": None,
                              "witness_ref": witness_for[(ns, path)]})
    control_files.sort(key=lambda r: (r["namespace"], r["path"]))
    stability = roots + sorted(controls, key=lambda r: (r["namespace"], r["path"]))

    def in_scope(ns: str, path: str) -> list[dict[str, Any]]:
        return [r for r in stability if r["namespace"] == ns and
                (path == "" or r.get("path", "") == path or r.get("path", "").startswith(path + "/"))]

    scopes = {
        "git-dir-control": {"scope_namespace": "git_dir", "scope_path": "", "scope_state": "present",
                            "entry_count": len(in_scope("git_dir", "")),
                            "scope_stability_sha256": _stream_digest(in_scope("git_dir", ""))},
        "common-dir-control": {"scope_namespace": "common_dir", "scope_path": "", "scope_state": "present",
                               "entry_count": len(in_scope("common_dir", "")),
                               "scope_stability_sha256": _stream_digest(in_scope("common_dir", ""))},
        "common-refs": {"scope_namespace": "common_dir", "scope_path": "refs", "scope_state": "present",
                        "entry_count": 1, "scope_stability_sha256": _stream_digest(in_scope("common_dir", "refs"))},
        "common-replace-refs": {"scope_namespace": "common_dir", "scope_path": "refs/replace",
                                "scope_state": "absent", "witness_ref": witness_for[("common_dir", "refs/replace")],
                                "entry_count": 0, "scope_stability_sha256": EMPTY_SHA256},
        "worktree-control": {"scope_namespace": "worktree", "scope_path": "", "scope_state": "present",
                             "entry_count": 1, "scope_stability_sha256": _stream_digest(in_scope("worktree", ""))},
    }
    empty_out = EMPTY_SHA256
    command_ids = ["ls-files-stage", "ls-files-v", "ls-files-t", "ls-tree-head", "status"] + ["cat-file-blob"] * 14
    command_results = [
        {"command_id": cid, "argv": ["git", cid, str(i)], "returncode": 0,
         "stdout_byte_count": 0, "stdout_sha256": empty_out,
         "stderr_byte_count": 0, "stderr_sha256": EMPTY_SHA256, "record_count": 0}
        for i, cid in enumerate(command_ids)
    ]
    config_entries = [
        {"namespace": "common_dir", "key": "core.bare", "value_sha256": "2ed27c1421e6928dbe13dbfdb5c59e1045b30341fe7ebe05700006bc5ac572c0"},
        {"namespace": "common_dir", "key": "core.repositoryformatversion", "value_sha256": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa"},
    ]
    config_stream_sha = _stream_digest(config_entries)
    config_sha = next(r["sha256"] for r in control_files if r["namespace"] == "common_dir" and r["path"] == "config")
    proof_observations: dict[str, list[dict[str, Any]]] = {
        "index-lock": [{"witness_ref": witness_for[("git_dir", "index.lock")]}],
        "head-lock": [{"witness_ref": witness_for[("git_dir", "HEAD.lock")]}],
        "config-lock": [{"witness_ref": witness_for[("common_dir", "config.lock")]}],
        "packed-refs-lock": [{"witness_ref": witness_for[("common_dir", "packed-refs.lock")]}],
        "shallow-lock": [{"witness_ref": witness_for[("common_dir", "shallow.lock")]}],
        "refs-locks": [{"scope_ref": "common-refs"}],
        "alternates": [{"witness_ref": witness_for[("common_dir", "objects/info/alternates")]}],
        "grafts": [{"witness_ref": witness_for[("common_dir", "info/grafts")]}],
        "replace-refs": [{"scope_ref": "common-replace-refs"}],
        "common-info-attributes": [{"witness_ref": witness_for[("common_dir", "info/attributes")]}],
        "git-info-attributes": [{"witness_ref": witness_for[("git_dir", "info/attributes")]}],
        "worktree-attributes": [{"scope_ref": "worktree-control"}],
    }
    projections = {"gitlinks": "ls-tree-head", "assume-unchanged": "ls-files-v", "skip-worktree": "ls-files-t"}
    for rule, cid in projections.items():
        proof_observations[rule] = [{"command_id": cid, "source_kind": "index", "record_count": 0,
                                      "stdout_byte_count": 0, "stdout_sha256": empty_out, "offender_count": 0}]
    config_observation = {"config_sha256": config_sha, "config_worktree_sha256": None,
                          "normalized_entry_digest": config_stream_sha}
    proof_observations["config-includes"] = [config_observation]
    proof_observations["unknown-config"] = [copy.deepcopy(config_observation)]
    rule_order = ["index-lock", "head-lock", "config-lock", "packed-refs-lock", "shallow-lock",
                  "refs-locks", "alternates", "grafts", "replace-refs", "common-info-attributes",
                  "git-info-attributes", "worktree-attributes", "gitlinks", "assume-unchanged",
                  "skip-worktree", "config-includes", "unknown-config"]
    absence_proofs = []
    for rule in rule_order:
        observations = proof_observations[rule]
        header = {"rule_id": rule, "passed": True, "observation_count": len(observations)}
        absence_proofs.append({**header, "observation_sha256": _stream_digest([header, *observations])})
    descriptor = {
        "schema_version": "turnvector.benchmark.repository-control.v1",
        "strict_parser_version": "canonical-strict-parser-v1", "control_name_key_rule": "ascii-casefold-v1",
        "worktree_kind": worktree_kind,
        "worktree_git_entry": {"kind": "gitfile", "byte_count": 20,
                               "sha256": synthetic_digest("gitfile"), "no_follow_identity": _identity(20, 4)},
        "worktree_identity": {"absolute_path": roots[0]["absolute_path"], **{k: roots[0][k] for k in _identity(0, 1)}},
        "git_dir_identity": {"absolute_path": roots[1]["absolute_path"], **{k: roots[1][k] for k in _identity(0, 1)}},
        "common_dir_identity": {"absolute_path": roots[2]["absolute_path"], **{k: roots[2][k] for k in _identity(0, 1)}},
        "control_files": control_files, "ignore_files": [], "config_entries": config_entries,
        "component_probe_witnesses": dict(sorted(witnesses.items())), "recursive_scope_proofs": scopes,
        "absence_proofs": absence_proofs, "proof_observations": proof_observations,
        "scan_counts": {"entry_count": len(stability), "path_bytes": sum(len(r.get("path", "")) for r in stability),
                        "name_bytes_max_observed": 20, "directory_entries_max_observed": 10,
                        "sort_index_bytes_max_observed": 80, "ignore_file_count": 0, "ignore_bytes": 0},
        "command_results": command_results, "stability_entries": stability,
        "observed_head": observed_head, "clean": True,
        "stability_sha256": _stream_digest(stability), "qualified_preflight": True,
    }
    return descriptor


@dataclass
class CompilerFixture:
    snapshot: dict[str, Any]
    reconciliation: dict[str, Any]
    expectation: dict[str, Any]
    catalog: list[dict[str, Any]]
    ledger: dict[str, Any]
    snapshot_bytes: bytes
    reconciliation_bytes: bytes
    expectation_bytes: bytes
    catalog_bytes: bytes
    ledger_bytes: bytes
    generated_case_ids: list[str]
    variant: str

    def clone(self) -> "CompilerFixture":
        return copy.deepcopy(self)

    @property
    def input_buffers(self) -> tuple[bytes, bytes, bytes, bytes, bytes]:
        return (self.snapshot_bytes, self.reconciliation_bytes, self.expectation_bytes,
                self.catalog_bytes, self.ledger_bytes)

    def refresh(self) -> None:
        self.snapshot_bytes = compact(self.snapshot)
        self.reconciliation_bytes = pretty(self.reconciliation)
        self.expectation_bytes = expectation_bytes(self.expectation, live=self.variant in {"base", "c4"})
        self.catalog_bytes = jsonl(self.catalog)
        self.ledger_bytes = pretty(self.ledger)


def build_fixture(*, variant: str = "base") -> CompilerFixture:
    if variant not in {"base", "f46", "b2", "c4"}:
        raise ValueError(f"unknown compiler fixture variant: {variant}")
    live_expectation = json.loads(EXPECTATION_PATH.read_text(encoding="utf-8"))
    expectation = expectation_variant(live_expectation) if variant in {"f46", "b2"} else live_expectation
    exp_bytes = expectation_bytes(expectation, live=variant in {"base", "c4"})
    reconciliation_bytes = RECONCILIATION_PATH.read_bytes()
    reconciliation = json.loads(reconciliation_bytes)
    cases = case_ids(expectation)
    assert len(cases) == 425 and len(set(cases)) == 425

    records: list[dict[str, Any]] = []
    obligation_by_lane: dict[str, list[str]] = {}
    source_records, section_records = [], []
    ordinal = 0
    for lane in expectation["lanes"]:
        path = f"source/{lane['id']}.txt"
        raw = (SOURCE_ROOT / f"{lane['id']}.txt").read_bytes()
        source_records.append({"path": path, "byte_count": len(raw), "sha256": digest(raw),
                               "file_type": "regular", "no_follow_identity": _identity(len(raw), 100 + ordinal)})
        start = 0
        lane_obligations = []
        for case, line in zip(lane["cases"], raw.splitlines(keepends=True)):
            ordinal += 1
            end = start + len(line)
            oid = (long_identifier("obligation", ordinal, 1001) if variant in {"f46", "b2"}
                   else f"fixture-obligation-{ordinal:04d}")
            lane_obligations.append(oid)
            section_records.append({"path": path, "start": start, "end": end, "sha256": digest(line)})
            records.append({
                "behavior_case_id": case["id"], "blocker_ids": [], "claim_class": f"fixture-claim-{ordinal:04d}",
                "design_gate_revision": DESIGN_REVISION, "evidence_grade": lane["id"], "id": oid,
                "invalidation_rule": "fixture source section drift invalidates this obligation", "kind": "obligation",
                "lane_id": lane["id"], "module_ids": [lane["id"]], "observable_seam": f"fixture-seam-{ordinal:04d}",
                "readiness_status": "design_ready", "required": True, "seam_id": f"fixture-seam-{ordinal:04d}",
                "section_end": end, "section_sha256": digest(line), "section_start": start,
                "source_file_sha256": digest(raw), "source_path": path,
            })
            start = end
        obligation_by_lane[lane["id"]] = lane_obligations
    assert ordinal == 46
    catalog_header = {
        "compile_custody_lineage_id": "tvb-qualification-d0-catalog-v1",
        "compile_custody_policy_sha256": POLICY_SHA256, "custody_domain_id": "fixture-custody-domain-v1",
        "custody_domain_sha256": CUSTODY_SHA256, "design_gate_revision": DESIGN_REVISION,
        "expectation_sha256": digest(exp_bytes), "id": "fixture-catalog-v1", "kind": "catalog",
        "lineage_id": "fixture-catalog-lineage-v1", "predecessor": None,
        "profile_id": "turnvector-implementation-v2", "record_count": 47,
        "required_obligation_count": 46, "schema_version": "turnvector.benchmark.obligation-catalog.v1",
        "source_reconciliation_sha256": digest(reconciliation_bytes), "t_max": 8,
    }
    catalog = [catalog_header, *records]
    catalog_bytes = jsonl(catalog)

    lane_ids = [lane["id"] for lane in expectation["lanes"]]
    gates_by_lane = {lane["id"]: [gate["id"] for gate in lane["gates"]] for lane in expectation["lanes"]}
    judges = [(long_identifier("judge", i, 1006) if variant in {"f46", "b2"} else f"fixture-judge-{i:04d}")
              for i in range(1, 47)]
    if variant == "c4":
        judges[0] = FIRST_CASE_ID
    bundles = [(long_identifier("bundle", i, 1005) if variant in {"f46", "b2"} else f"fixture-bundle-{i:04d}")
               for i in range(1, 13)]
    sources = [f"fixture-source-{i:04d}" for i in range(1, 42)]
    obligation_to_judge = [{"obligation_id": record["id"], "judge_id": judge}
                            for record, judge in zip(records, judges)]
    bundle_records = [{"id": bundle, "lane_id": lane} for lane, bundle in zip(lane_ids, bundles)]
    memberships, source_cursor = [], 0
    for bundle, count in zip(bundles, LANE_RAW_COUNTS):
        selected = [sources[(source_cursor + i) % len(sources)] for i in range(count)]
        source_cursor += count
        memberships.extend({"bundle_id": bundle, "evidence_source_id": source} for source in selected)
    memberships.sort(key=lambda r: (r["bundle_id"], r["evidence_source_id"]))
    lane_contexts = []
    outputs = []
    for lane, cc, oc in zip(lane_ids, LANE_CONTEXT_COUNTS, LANE_OUTPUT_COUNTS):
        lane_contexts.extend({"lane_id": lane, "artifact_type": a} for a in (["manifest"] if cc == 1 else ["environment", "manifest"]))
        outputs.extend({"lane_id": lane, "artifact_type": a} for a in (["report"] if oc == 1 else ["checksums", "report"]))
    gate_records = sorted(({"lane_id": lane, "gate_id": gate} for lane in lane_ids for gate in gates_by_lane[lane]),
                          key=lambda r: (r["lane_id"], r["gate_id"]))
    pairs = []
    m_by_lane = []
    for lane in lane_ids:
        obligations, gates = obligation_by_lane[lane], gates_by_lane[lane]
        if variant in {"f46", "b2"}:
            lane_pairs = [(o, g) for o in obligations for g in gates]
        else:
            count = max(len(obligations), len(gates))
            lane_pairs = [(obligations[i % len(obligations)], gates[i % len(gates)]) for i in range(count)]
        m_by_lane.append(len(lane_pairs))
        pairs.extend({"lane_id": lane, "obligation_id": o, "gate_id": g} for o, g in lane_pairs)
    pairs.sort(key=lambda r: (r["lane_id"], r["obligation_id"], r["gate_id"]))
    M = sum(m_by_lane)
    H = sum(c * m for c, m in zip(LANE_CASE_COUNTS, m_by_lane))
    R_HE = sum(c * m * d for c, m, d in zip(LANE_CASE_COUNTS, m_by_lane, LANE_RAW_COUNTS))
    judge_negative = sorted(({"owner_id": record["id"], "id": f"fixture-template-judge-{i:04d}"}
                             for i, record in enumerate(records, 1)), key=lambda r: (r["owner_id"], r["id"]))
    aggregate = sorted(({"owner_id": gate["gate_id"], "id": f"fixture-template-aggregate-{i:04d}"}
                        for i, gate in enumerate(gate_records, 1)), key=lambda r: (r["owner_id"], r["id"]))
    plumbing = sorted(({"owner_id": gate["gate_id"], "id": f"fixture-template-plumbing-{i:04d}"}
                       for i, gate in enumerate(gate_records, 1)), key=lambda r: (r["owner_id"], r["id"]))
    cardinalities = {
        "lane_count": 12, "case_plan_count": 425, "gate_count": 58, "obligation_count": 46,
        "judge_count": 46, "evidence_bundle_count": 12, "evidence_source_count": 41,
        "lane_context_membership_count": 21, "post_gate_output_membership_count": 22,
        "artifact_membership_total": 95, "judge_negative_template_count": 46,
        "aggregate_negative_template_count": 58, "plumbing_negative_template_count": 58,
        "lane_obligation_counts": LANE_OBLIGATION_COUNTS, "lane_case_counts": LANE_CASE_COUNTS,
        "lane_gate_counts": LANE_GATE_COUNTS, "lane_raw_membership_counts": LANE_RAW_COUNTS,
        "lane_context_counts": LANE_CONTEXT_COUNTS, "lane_output_counts": LANE_OUTPUT_COUNTS,
        "obligation_gate_pair_count": M, "enforcement_path_count": H,
        "evidence_membership_record_count": 52, "path_evidence_record_count": R_HE,
        "endpoint_reference_count": 11 * H + 2 * R_HE + 2 * M + 630,
        "relation_record_count": 4 * H + R_HE + M + 315,
        "entity_record_count": H + 852,
        "key_count_exec": 425 + R_HE + 3 * H + 4 * 58 + 21 + 22,
    }
    bind = lambda keys, domain: {key: synthetic_digest(f"{domain}:{key}") for key in keys}
    binds = {
        "source_reconciliation_sha256": digest(reconciliation_bytes), "expectation_sha256": digest(exp_bytes),
        "catalog_sha256": digest(catalog_bytes), "compile_custody_policy_sha256": POLICY_SHA256,
        "custody_domain_sha256": CUSTODY_SHA256,
        "lane_suite_sha256": bind(lane_ids, "suite"), "case_schema_sha256": bind(lane_ids, "case-schema"),
        "judge_contract_sha256": bind([r["id"] for r in records], "judge"),
        "evidence_bundle_contract_sha256": bind(lane_ids, "bundle"),
        "raw_evidence_source_sha256": bind(sources, "source"),
        "lane_context_contract_sha256": bind(["manifest", "environment"], "context"),
        "post_gate_output_contract_sha256": bind(["report", "checksums"], "output"),
        "gate_sha256": bind([r["gate_id"] for r in gate_records], "gate"),
        "judge_negative_sha256": bind([r["id"] for r in judge_negative], "judge-negative"),
        "aggregate_negative_sha256": bind([r["id"] for r in aggregate], "aggregate-negative"),
        "plumbing_negative_sha256": bind([r["id"] for r in plumbing], "plumbing-negative"),
    }
    entities = {
        "judge_ids": sorted(judges), "obligation_to_judge": sorted(obligation_to_judge, key=lambda r: (r["obligation_id"], r["judge_id"])),
        "evidence_bundles": sorted(bundle_records, key=lambda r: (r["lane_id"], r["id"])),
        "evidence_source_ids": sorted(sources), "evidence_bundle_memberships": memberships,
        "lane_contexts": sorted(lane_contexts, key=lambda r: (r["lane_id"], r["artifact_type"])),
        "post_gate_outputs": sorted(outputs, key=lambda r: (r["lane_id"], r["artifact_type"])),
        "gates": gate_records, "obligation_gate_pairs": pairs,
        "judge_negative_tests": judge_negative, "aggregate_negative_tests": aggregate,
        "plumbing_negative_tests": plumbing,
    }
    ledger = {"schema_version": "turnvector.benchmark.traceability.v1", "id": "fixture-ledger-v1",
              "profile_id": "turnvector-implementation-v2", "design_gate_revision": DESIGN_REVISION,
              "predecessor": None, "cardinalities": cardinalities, "binds": binds, "entities": entities}
    ledger_bytes = pretty(ledger)

    historical = []
    for mapping in reconciliation["mappings"]:
        historical.extend([
            {"revision": mapping["old_revision"], "path": mapping["old_path"], "byte_count": 1,
             "sha256": mapping["old_sha256"], "git_object_type": "blob"},
            {"revision": mapping["current_revision"], "path": mapping["current_path"], "byte_count": 1,
             "sha256": mapping["current_sha256"], "git_object_type": "blob"},
        ])
    historical.sort(key=lambda r: (r["revision"], r["path"]))
    snapshot = {
        "schema_version": "turnvector.benchmark.authority-snapshot.v1", "repository": "TurnVector",
        "revision": expectation["source_contract"]["revision"], "observed_head": expectation["source_contract"]["revision"],
        "revision_relation": "exact", "ancestry_verified": False, "clean_required": True, "clean": True,
        "source_files": sorted(source_records, key=lambda r: r["path"]),
        "section_records": sorted(section_records, key=lambda r: (r["path"], r["start"], r["end"])),
        "historical_objects": historical,
        "repository_control": repository_control(expectation["source_contract"]["revision"]),
    }
    snapshot_bytes = compact(snapshot)
    fixture = CompilerFixture(snapshot, reconciliation, expectation, catalog, ledger, snapshot_bytes,
                              reconciliation_bytes, exp_bytes, catalog_bytes, ledger_bytes, cases, variant)
    expected_collision = {FIRST_CASE_ID} if variant == "c4" else set()
    documents: list[Any] = [snapshot, reconciliation, expectation, *catalog, ledger]
    prior = {text for document in documents for text in strings(document)
             if IDENTIFIER.fullmatch(text) and not HEX64.fullmatch(text)}
    intersection = prior.intersection(cases)
    assert intersection == expected_collision, (variant, intersection)
    return fixture


def mutate_and_refresh(fixture: CompilerFixture, mutator: Callable[[CompilerFixture], None]) -> CompilerFixture:
    result = fixture.clone()
    mutator(result)
    result.refresh()
    return result
