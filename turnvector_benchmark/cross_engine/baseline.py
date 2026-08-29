"""Immutable cross-engine baseline promotion and applicability checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from turnvector_benchmark.core import ContractError


BASELINE_SCHEMA = "turnvector.benchmark.cross-engine-baseline-receipt.v1"
BASELINE_COMMIT_FILE = "COMMITTED"
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _timestamp(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ContractError("%s must be RFC 3339 with a timezone" % where)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ContractError("%s must be RFC 3339 with a timezone" % where) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("%s must be RFC 3339 with a timezone" % where)
    return parsed


def _write_staged(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _cleanup_staging(path: Path) -> None:
    for name in (BASELINE_COMMIT_FILE, "inventory.json", "receipt.json"):
        try:
            (path / name).unlink()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def _digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ContractError("%s must be a lowercase SHA-256" % where)
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError("%s must be a non-empty string" % where)
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    except (TypeError, ValueError, UnicodeError) as error:
        raise ContractError("baseline receipt is not canonical JSON") from error


def evidence_tree_digest(root: Path) -> Tuple[str, Tuple[Mapping[str, Any], ...]]:
    root = root.resolve()
    if not root.is_dir():
        raise ContractError("baseline evidence root must be a directory")
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ContractError("baseline evidence must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ContractError("baseline evidence must contain regular files only")
        raw = path.read_bytes()
        records.append({"path": relative, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    if not records:
        raise ContractError("baseline evidence root must not be empty")
    stream = b"".join(_canonical(record) for record in records)
    return hashlib.sha256(stream).hexdigest(), tuple(records)


@dataclass(frozen=True)
class BaselineReceipt:
    authority_id: str
    promoted_at: str
    evidence_root_sha256: str
    profile_sha256: str
    scenario_set_sha256: str
    target_sha256: str
    model_sha256: str
    physical_host_sha256: str
    superseded_baseline_sha256: Optional[str]

    def as_dict(self) -> Mapping[str, Any]:
        value: Dict[str, Any] = {
            "schema_version": BASELINE_SCHEMA,
            "authority_id": _text(self.authority_id, "authority_id"),
            "promoted_at": _text(self.promoted_at, "promoted_at"),
            "evidence_root_sha256": _digest(self.evidence_root_sha256, "evidence_root_sha256"),
            "profile_sha256": _digest(self.profile_sha256, "profile_sha256"),
            "scenario_set_sha256": _digest(self.scenario_set_sha256, "scenario_set_sha256"),
            "target_sha256": _digest(self.target_sha256, "target_sha256"),
            "model_sha256": _digest(self.model_sha256, "model_sha256"),
            "physical_host_sha256": _digest(self.physical_host_sha256, "physical_host_sha256"),
            "superseded_baseline_sha256": self.superseded_baseline_sha256,
        }
        if self.superseded_baseline_sha256 is not None:
            _digest(self.superseded_baseline_sha256, "superseded_baseline_sha256")
        _timestamp(self.promoted_at, "promoted_at")
        return value

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical(self.as_dict())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


def promote_baseline(
    registry: Path,
    baseline_id: str,
    evidence_root: Path,
    *,
    authority_id: str,
    identities: Mapping[str, str],
    superseded_baseline_sha256: Optional[str] = None,
    promoted_at: Optional[str] = None,
) -> BaselineReceipt:
    if not baseline_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for c in baseline_id):
        raise ContractError("baseline ID is invalid")
    required = {"profile_sha256", "scenario_set_sha256", "target_sha256", "model_sha256", "physical_host_sha256"}
    if set(identities) != required:
        raise ContractError("baseline identities must have the exact required key set")
    root_digest, records = evidence_tree_digest(evidence_root)
    receipt = BaselineReceipt(
        authority_id=authority_id,
        promoted_at=promoted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        evidence_root_sha256=root_digest,
        profile_sha256=identities["profile_sha256"],
        scenario_set_sha256=identities["scenario_set_sha256"],
        target_sha256=identities["target_sha256"],
        model_sha256=identities["model_sha256"],
        physical_host_sha256=identities["physical_host_sha256"],
        superseded_baseline_sha256=superseded_baseline_sha256,
    )
    registry = registry.resolve()
    registry.mkdir(parents=True, exist_ok=True)
    target = registry / baseline_id
    receipt_bytes = receipt.canonical_bytes
    inventory_bytes = _canonical({"records": list(records)})
    commit_bytes = (receipt.sha256 + "\n").encode("ascii")

    try:
        staging = Path(tempfile.mkdtemp(prefix=".%s." % baseline_id, dir=str(registry)))
    except OSError as error:
        raise ContractError("baseline promotion staging failed") from error

    lock_path = registry / (".%s.promotion.lock" % baseline_id)
    lock_fd: Optional[int] = None
    committed = False
    try:
        _write_staged(staging / "inventory.json", inventory_bytes)
        _write_staged(staging / "receipt.json", receipt_bytes)
        _write_staged(staging / BASELINE_COMMIT_FILE, commit_bytes)
        try:
            lock_fd = os.open(
                lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError as error:
            raise ContractError(
                "baseline already exists or promotion is in progress and cannot be overwritten"
            ) from error
        if target.exists() or target.is_symlink():
            raise ContractError("baseline already exists and cannot be overwritten")
        # Every file, including the commit marker, is complete before this
        # single directory rename publishes the baseline.  The create-only lock
        # serializes cooperating promoters, so readers see either no target or
        # the complete immutable baseline, never a legal partial directory.
        os.rename(staging, target)
        committed = True
    except ContractError:
        raise
    except OSError as error:
        raise ContractError("baseline promotion failed before commit") from error
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError:
                pass
        if not committed:
            _cleanup_staging(staging)
    return receipt


def baseline_applicability(
    receipt: BaselineReceipt,
    candidate_started_at: str,
    identities: Mapping[str, str],
    *,
    candidate_evidence_root_sha256: Optional[str] = None,
) -> Tuple[bool, Tuple[str, ...]]:
    value = receipt.as_dict()
    reasons = []
    candidate_start = _timestamp(candidate_started_at, "candidate start")
    promoted = _timestamp(receipt.promoted_at, "promoted_at")
    if promoted >= candidate_start:
        reasons.append("baseline_not_promoted_before_candidate")
    if candidate_evidence_root_sha256 is not None:
        candidate_digest = _digest(
            candidate_evidence_root_sha256, "candidate_evidence_root_sha256"
        )
        if candidate_digest == receipt.evidence_root_sha256:
            reasons.append("baseline_same_run_denominator")
    for field in ("profile_sha256", "scenario_set_sha256", "target_sha256", "model_sha256", "physical_host_sha256"):
        if identities.get(field) != value[field]:
            reasons.append("baseline_identity_mismatch:%s" % field)
    return not reasons, tuple(reasons)
