from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..core import ContractError, canonical_json
from ..evidence import sha256_file
from .common import (
    artifact_path,
    validate_artifacts,
    validate_environment,
    validate_host_samples,
    validate_limits,
    validate_run_manifest,
    validate_session,
)
from .contract import (
    EVIDENCE_SCHEMA,
    REPORT_SCHEMA,
    GatewayValidationContract,
    _object,
    _read_json,
    _strict_keys,
)
from .lifecycle import validate_lifecycle
from .transport import validate_transport


def validate_gateway_evidence(
    contract: GatewayValidationContract, path: Path
) -> Mapping[str, Any]:
    evidence_path = path.resolve()
    obj = _read_json(evidence_path, "Gateway validation evidence")
    keys = [
        "schema_version",
        "contract",
        "session",
        "environment",
        "effective_limits",
        "artifacts",
    ]
    _strict_keys(obj, keys, [], str(evidence_path))
    if obj["schema_version"] != EVIDENCE_SCHEMA:
        raise ContractError(f"{evidence_path}.schema_version must be {EVIDENCE_SCHEMA!r}")
    identity = _object(obj["contract"], "evidence.contract")
    _strict_keys(identity, ["id", "sha256"], [], "evidence.contract")
    if identity != {"id": contract.contract_id, "sha256": contract.sha256}:
        raise ContractError("Gateway evidence is bound to a different contract identity")
    session, reasons = validate_session(obj["session"], contract)
    environment, environment_reasons = validate_environment(obj["environment"])
    reasons.extend(environment_reasons)
    limits = validate_limits(obj["effective_limits"])
    limits_sha256 = hashlib.sha256(canonical_json(limits).encode("ascii")).hexdigest()
    if obj["session"]["effective_limits_sha256"] != limits_sha256:
        raise ContractError("effective limits do not match their session identity")
    artifacts = validate_artifacts(contract, evidence_path, obj["artifacts"])
    validate_run_manifest(
        artifact_path(evidence_path, artifacts["run_manifest"]),
        contract,
        obj["session"],
        limits,
    )
    lifecycle, lifecycle_reasons = validate_lifecycle(
        contract, artifact_path(evidence_path, artifacts["lifecycle_trace"]), limits
    )
    transport, transport_reasons, trial_count = validate_transport(
        contract, artifact_path(evidence_path, artifacts["transport_trials"])
    )
    validate_host_samples(artifact_path(evidence_path, artifacts["host_samples"]))
    reasons.extend(lifecycle_reasons)
    reasons.extend(transport_reasons)
    reasons = sorted(set(reasons))
    if reasons:
        status = "not_publishable"
    elif session["subject_kind"] == "fixture":
        status = "not_claimable_fixture"
    else:
        status = "publishable"
    claimable = status == "publishable"
    return {
        "schema_version": REPORT_SCHEMA,
        "status": status,
        "contract_id": contract.contract_id,
        "contract_sha256": contract.sha256,
        "evidence_path": str(evidence_path),
        "evidence_sha256": sha256_file(evidence_path),
        "session": session,
        "environment": environment,
        "effective_limits": dict(limits),
        "artifact_count": len(artifacts),
        "lifecycle": {
            "status": "passed" if not lifecycle_reasons else "failed",
            "case_count": len(lifecycle),
            "cases": lifecycle,
        },
        "transport": {
            "status": "measured_baseline" if not transport_reasons else "failed",
            "case_count": len(transport),
            "trial_count": trial_count,
            "cases": transport,
        },
        "reasons": reasons,
        "claims": {
            "backend_response_lifetimes_decoupled": claimable,
            "per_request_uds_baseline_measured": claimable,
            "pooling_qualified": False,
            "ownership_change_authorized": False,
        },
    }
