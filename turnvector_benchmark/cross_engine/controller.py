from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError
from .adapters import lifecycle_adapter_argv, resolve_target_adapter
from .campaign import AttemptLedger, CampaignCell, CampaignPlan
from .collectors import HostProcessCollector
from .lifecycle import EngineLifecycleClient


CAPABILITY_STATUSES = frozenset(
    {
        "supported",
        "capability_unsupported",
        "profile_incompatible",
        "not_applicable",
        "environment_unavailable",
    }
)
MANDATORY_COMMON_CAPABILITIES = (
    "openai_chat_completions",
    "streaming_sse",
    "usage_accounting_or_benchmark_tokenizer",
)


@dataclass(frozen=True)
class PreflightDecision:
    contract_status: str
    capability_status: str
    execution_status: str
    evidence_status: str
    promotion_status: str
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class CellResult:
    cell_id: str
    target_id: str
    contract_status: str
    capability_status: str
    execution_status: str
    evidence_status: str
    promotion_status: str
    primary_attempt_ordinal: Optional[int]
    reason_code: Optional[str]
    observations: Mapping[str, Any]
    diagnostics: Tuple[Mapping[str, str], ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "target_id": self.target_id,
            "contract_status": self.contract_status,
            "capability_status": self.capability_status,
            "execution_status": self.execution_status,
            "evidence_status": self.evidence_status,
            "promotion_status": self.promotion_status,
            "primary_attempt_ordinal": self.primary_attempt_ordinal,
            "reason_code": self.reason_code,
            "observations": dict(self.observations),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }


@dataclass(frozen=True)
class CrossEngineControllerResult:
    campaign_id: str
    cells: Tuple[CellResult, ...]
    attempts_path: Path

    def as_dict(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "cells": [cell.as_dict() for cell in self.cells],
            "attempts_path": self.attempts_path.name,
        }


class RetryableAttemptError(RuntimeError):
    """A predeclared contract/environment-invalid attempt, never performance retry."""

    def __init__(self, status: str, reason_code: str, message: str) -> None:
        if status not in {"contract_invalid", "environment_invalid"}:
            raise ContractError("retryable attempt status must be contract/environment invalid")
        super().__init__(message)
        self.status = status
        self.reason_code = reason_code


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def decide_preflight(
    *,
    required_capabilities: Sequence[str],
    declared_capabilities: Mapping[str, bool],
    probed_capabilities: Mapping[str, bool],
    profile_compatible: bool = True,
    environment_available: bool = True,
    applicable: bool = True,
    mandatory_capabilities: Sequence[str] = MANDATORY_COMMON_CAPABILITIES,
) -> PreflightDecision:
    """Apply declaration -> probe -> frozen-plan capability authority exactly once."""
    if not applicable:
        return PreflightDecision(
            "valid", "not_applicable", "not_started", "not_evaluated", "not_applicable", "scenario_not_applicable"
        )
    if not environment_available:
        return PreflightDecision(
            "valid", "environment_unavailable", "not_started", "not_evaluated", "not_applicable", "preflight_environment_unavailable"
        )
    if not profile_compatible:
        return PreflightDecision(
            "valid", "profile_incompatible", "not_started", "not_evaluated", "not_applicable", "openai_dialect_incompatible"
        )
    all_required = tuple(dict.fromkeys((*mandatory_capabilities, *required_capabilities)))
    for capability in all_required:
        declared = declared_capabilities.get(capability, False)
        if not declared:
            status = (
                "profile_incompatible"
                if capability in mandatory_capabilities
                else "capability_unsupported"
            )
            reason = (
                "mandatory_common_capability_missing"
                if capability in mandatory_capabilities
                else "required_capability_not_declared"
            )
            return PreflightDecision(
                "valid", status, "not_started", "not_evaluated", "not_applicable", reason
            )
        if capability not in probed_capabilities:
            return PreflightDecision(
                "invalid", "supported", "not_started", "not_evaluated", "not_evaluated", "capability_probe_missing"
            )
        if probed_capabilities[capability] is not True:
            return PreflightDecision(
                "invalid", "supported", "not_started", "not_evaluated", "not_evaluated", "capability_declaration_probe_mismatch"
            )
    return PreflightDecision(
        "valid", "supported", "not_started", "not_evaluated", "not_evaluated", None
    )


def _capability_bools(value: Any, where: str) -> Mapping[str, bool]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{where} must be an object")
    parsed: Dict[str, bool] = {}
    for name, disposition in value.items():
        if isinstance(disposition, bool):
            parsed[name] = disposition
        elif isinstance(disposition, Mapping) and disposition.get("status") in {
            "supported",
            "capability_unsupported",
        }:
            parsed[name] = disposition["status"] == "supported"
        else:
            raise ContractError(f"{where}.{name} has an invalid capability disposition")
    return parsed


def _sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _endpoint_config(target: Any, target_argv: Sequence[str]) -> Mapping[str, Any]:
    endpoint = _field(target, "endpoint")
    if not isinstance(endpoint, Mapping):
        raise ContractError("target endpoint must be a closed object")
    admitted = {
        field: endpoint[field]
        for field in (
            "protocol_family",
            "protocol_version",
            "transport",
            "api_flavor",
            "stream_format",
            "authentication_env_var",
        )
        if field in endpoint
    }
    required = {
        "protocol_family",
        "protocol_version",
        "transport",
        "api_flavor",
        "stream_format",
        "authentication_env_var",
    }
    if set(admitted) != required:
        raise ContractError("target endpoint is missing the OpenAI serving descriptor core")
    if "base_url" in endpoint:
        for field in ("base_url", "model_ids", "process_ids", "capability_report_sha256"):
            if field not in endpoint:
                raise ContractError("ready endpoint descriptor is incomplete")
            admitted[field] = endpoint[field]
        return admitted
    try:
        host = target_argv[target_argv.index("--host") + 1]
        port = int(target_argv[target_argv.index("--port") + 1])
    except (ValueError, IndexError) as error:
        raise ContractError("registered target argv lacks a loopback host and port") from error
    model = _field(target, "model", {})
    model_id = _field(model, "id")
    if (
        _field(target, "engine_family") == "mlx-lm"
        and _field(target, "manifest_purpose") == "publication"
    ):
        try:
            model_id = target_argv[target_argv.index("--model") + 1]
        except (ValueError, IndexError) as error:
            raise ContractError("mlx-lm target argv lacks its runtime model path") from error
    if not isinstance(model_id, str) or not model_id:
        raise ContractError("target model must declare an ID")
    admitted.update(
        {
            "base_url": f"http://{host}:{port}/v1",
            "model_ids": [model_id],
            # The lifecycle runtime replaces this placeholder with the owned PID.
            "process_ids": [],
            "capability_report_sha256": _sha(_field(target, "capabilities", {})),
        }
    )
    return admitted


class CrossEngineController:
    """Benchmark-owned common-cell orchestration over fixed lifecycle adapters."""

    def __init__(
        self,
        *,
        campaign: CampaignPlan,
        targets: Mapping[str, Any],
        output_root: Path,
        scenario_executor: Any,
        capability_probe: Callable[[Any, CampaignCell], Mapping[str, Any]],
        lifecycle_factory: Callable[..., Any] = EngineLifecycleClient,
        collector_factory: Callable[..., Any] = HostProcessCollector,
        clock: Callable[[], int] = time.monotonic_ns,
        runtime_bindings: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> None:
        if output_root.exists():
            raise ContractError("cross-engine output root must be absent")
        missing = sorted({cell.target_id for cell in campaign.cells} - set(targets))
        if missing:
            raise ContractError(f"campaign targets are missing: {', '.join(missing)}")
        self.campaign = campaign
        self.targets = dict(targets)
        self.output_root = output_root
        self.scenario_executor = scenario_executor
        self.capability_probe = capability_probe
        self.lifecycle_factory = lifecycle_factory
        self.collector_factory = collector_factory
        self.clock = clock
        self.runtime_bindings = {
            target_id: dict(bindings)
            for target_id, bindings in (runtime_bindings or {}).items()
        }

    def _preflight(self, target: Any, cell: CampaignCell) -> PreflightDecision:
        observed = self.capability_probe(target, cell)
        if not isinstance(observed, Mapping):
            raise ContractError("capability probe must return an object")
        declared = _capability_bools(_field(target, "capabilities", {}), "target capabilities")
        probed = _capability_bools(observed.get("capabilities", observed), "preflight capabilities")
        return decide_preflight(
            required_capabilities=cell.required_capabilities,
            declared_capabilities=declared,
            probed_capabilities=probed,
            profile_compatible=observed.get("profile_compatible", True),
            environment_available=observed.get("environment_available", True),
            applicable=observed.get("applicable", True),
        )

    def _execute_scenario(
        self, cell: CampaignCell, endpoint: Mapping[str, Any], collector: Any
    ) -> Mapping[str, Any]:
        executor = self.scenario_executor
        if hasattr(executor, "run"):
            result = executor.run(cell=cell, endpoint=endpoint, collector=collector)
        else:
            result = executor(cell=cell, endpoint=endpoint, collector=collector)
        if not isinstance(result, Mapping):
            raise ContractError("scenario executor must return Benchmark-owned observations")
        forbidden = {"engine_summary", "target_summary", "diagnostic_import"} & set(result)
        if forbidden:
            raise ContractError("target-owned summaries cannot enter common reducers")
        return dict(result)

    def _run_attempt(
        self, target: Any, cell: CampaignCell, attempt_ordinal: int
    ) -> Tuple[Mapping[str, Any], Tuple[Mapping[str, str], ...]]:
        registration, target_argv = resolve_target_adapter(
            target, self.runtime_bindings.get(cell.target_id)
        )
        state_root = self.output_root / "state" / f"attempt-{attempt_ordinal:06d}"
        state_root.mkdir(parents=True, exist_ok=False)
        target_id = cell.target_id
        session_id = f"{self.campaign.campaign_id}.attempt-{attempt_ordinal:06d}"
        reset_policy = cell.isolation_policy
        target_sha = _field(target, "target_sha256", _sha(target))
        config_sha = _field(target, "config_sha256", _sha(_field(target, "adapter", {})))
        model_sha = _field(target, "model_sha256", _sha(_field(target, "model", {})))
        endpoint = _endpoint_config(target, target_argv)
        adapter = _field(target, "adapter", {})
        environment_names = _field(
            target,
            "environment_names",
            _field(adapter, "environment_names", ()),
        )
        if not isinstance(environment_names, Sequence) or isinstance(environment_names, (str, bytes)):
            raise ContractError("target environment_names must be a sequence")
        diagnostics: List[Mapping[str, str]] = []
        observations: Mapping[str, Any] = {}
        client = self.lifecycle_factory(lifecycle_adapter_argv(registration.command_id))
        started: Optional[Mapping[str, Any]] = None
        collector = None
        try:
            client.request(
                "hello",
                {"requested_adapter_protocol": "turnvector.benchmark.cross-engine-lifecycle.v1"},
            )
            prepared = client.request(
                "prepare_session",
                {
                    "run_id": self.campaign.campaign_id,
                    "session_id": session_id,
                    "state_root": str(state_root),
                    "target_sha256": target_sha,
                    "config_sha256": config_sha,
                    "model_sha256": model_sha,
                    "reset_policy": reset_policy,
                },
            )
            started = client.request(
                "start_target",
                {
                    "argv": list(target_argv),
                    "config": {
                        "command_id": registration.command_id,
                        "resolved_argv": list(target_argv),
                        "endpoint": dict(endpoint),
                    },
                    "environment_names": list(environment_names),
                    "readiness_deadline_ms": int(_field(target, "readiness_deadline_ms", 30000)),
                },
            )
            endpoint_ready = client.request(
                "describe_endpoint", {"target_id": target_id, "session_id": session_id}
            )
            process_ids = [record["pid"] for record in started["child_processes"]]
            collector = self.collector_factory(process_ids)
            collector.start()
            observations = self._execute_scenario(cell, endpoint_ready["endpoint"], collector)
            collection = collector.stop()
            collector = None
            observations["host_process_evidence"] = collection.as_dict()
            observations["listener_owner"] = dict(endpoint_ready["listener_owner"])
        finally:
            if collector is not None:
                try:
                    collector.stop()
                except Exception as error:
                    diagnostics.append({"kind": "collector_cleanup_failed", "message": str(error)[:1024]})
            if started is not None:
                try:
                    client.request(
                        "stop_target",
                        {
                            "reason": "cell_complete",
                            "deadline_ms": 15000,
                            "expected_process_group_leader_pid": started["process_group_leader_pid"],
                        },
                    )
                    client.request("shutdown", {"session_id": session_id})
                except Exception as error:
                    diagnostics.append({"kind": "lifecycle_cleanup_failed", "message": str(error)[:1024]})
            client.close()
        return observations, tuple(diagnostics)

    def run(self) -> CrossEngineControllerResult:
        self.output_root.mkdir(parents=True, exist_ok=False)
        (self.output_root / "state").mkdir()
        ledger = AttemptLedger(
            self.output_root / "attempts.jsonl",
            retryable_reason_codes=self.campaign.retryable_reason_codes,
        )
        results: List[CellResult] = []
        for cell in self.campaign.cells:
            target = self.targets[cell.target_id]
            decision = self._preflight(target, cell)
            if decision.contract_status != "valid" or decision.capability_status != "supported":
                results.append(
                    CellResult(
                        cell.cell_id,
                        cell.target_id,
                        decision.contract_status,
                        decision.capability_status,
                        decision.execution_status,
                        decision.evidence_status,
                        decision.promotion_status,
                        None,
                        decision.reason_code,
                        {},
                        (),
                    )
                )
                continue
            retry_of: Optional[int] = None
            observations: Mapping[str, Any] = {}
            diagnostics: Tuple[Mapping[str, str], ...] = ()
            reason: Optional[str] = None
            execution_status = "infrastructure_failed"
            contract_status = "valid"
            while True:
                started_ns = self.clock()
                try:
                    observations, diagnostics = self._run_attempt(
                        target, cell, len(ledger.records)
                    )
                    finished_ns = self.clock()
                    record = ledger.append(
                        cell_id=cell.cell_id,
                        status="completed",
                        reason_code=None,
                        retry_of=retry_of,
                        started_monotonic_ns=started_ns,
                        finished_monotonic_ns=finished_ns,
                    )
                    execution_status = "completed"
                    break
                except RetryableAttemptError as error:
                    finished_ns = self.clock()
                    record = ledger.append(
                        cell_id=cell.cell_id,
                        status=error.status,
                        reason_code=error.reason_code,
                        retry_of=retry_of,
                        started_monotonic_ns=started_ns,
                        finished_monotonic_ns=finished_ns,
                        details={"message": str(error)[:1024]},
                    )
                    reason = error.reason_code
                    if ledger.can_retry(record):
                        retry_of = record.attempt_ordinal
                        continue
                    contract_status = "invalid" if error.status == "contract_invalid" else "valid"
                    break
                except Exception as error:
                    finished_ns = self.clock()
                    record = ledger.append(
                        cell_id=cell.cell_id,
                        status="runtime_error",
                        reason_code="cell_execution_failed",
                        retry_of=retry_of,
                        started_monotonic_ns=started_ns,
                        finished_monotonic_ns=finished_ns,
                        details={"message": str(error)[:1024]},
                    )
                    reason = "cell_execution_failed"
                    diagnostics = ({"kind": "cell_execution_failed", "message": str(error)[:1024]},)
                    break
            primary = ledger.primary_attempt(cell.cell_id)
            results.append(
                CellResult(
                    cell.cell_id,
                    cell.target_id,
                    contract_status,
                    "supported",
                    execution_status,
                    "publishable" if execution_status == "completed" else "not_evaluated",
                    "not_evaluated",
                    None if primary is None else primary.attempt_ordinal,
                    reason,
                    observations,
                    diagnostics,
                )
            )
        return CrossEngineControllerResult(
            self.campaign.campaign_id, tuple(results), ledger.path
        )


__all__ = [
    "CAPABILITY_STATUSES",
    "CellResult",
    "CrossEngineController",
    "CrossEngineControllerResult",
    "MANDATORY_COMMON_CAPABILITIES",
    "PreflightDecision",
    "RetryableAttemptError",
    "decide_preflight",
]
