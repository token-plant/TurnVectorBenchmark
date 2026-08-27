"""Non-claimable same-process owner-lifecycle benchmark fixture.

Models exactly one daemon process containing a fake Device Executor. It never
claims a real Backend Interface, never launches or describes a separate MLX
Worker, and always publishes ``benchmark_fixture`` execution provenance with
``fixture_id=owner-lifecycle-device-executor-v1``. Every behavior is
deterministic, in-process, bounded, and fail-closed; fixture evidence is never
production evidence.

The successor owner-lifecycle lane preserves 24 CasePlans through the six
``daemon_outcome`` values by four ``client_protocol_relation`` values below.
PR 3 installed this fixture and its selection seam; PR 4 activates the lane
through the absorbing fixture-taint interlock, so selecting the fixture always
publishes ``benchmark_fixture`` provenance and makes the run
``not_claimable_fixture``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .core import ContractError
from .fixture_provenance import BENCHMARK_FIXTURE


OWNER_LIFECYCLE_FIXTURE_ID = "owner-lifecycle-device-executor-v1"
OWNER_LIFECYCLE_FIXTURE_SCHEMA = "turnvector.benchmark.owner-lifecycle-fixture.v1"

#: The frozen successor matrix domain: six daemon outcomes (six failure injections).
DAEMON_OUTCOMES: Tuple[str, ...] = (
    "normal",
    "failure_before_backend_initialization",
    "failure_during_turn",
    "safe_point_timeout",
    "malformed_client_frame",
    "duplicate_client_command",
)

#: The frozen successor matrix domain: four client protocol relations.
CLIENT_PROTOCOL_RELATIONS: Tuple[str, ...] = (
    "exact",
    "compatible",
    "incompatible",
    "unknown_capability",
)

#: Fixture transport bound used by the fake Device Executor (mirrors the
#: certification threshold it must satisfy; frames above it are rejected).
MAX_CLIENT_FRAME_BYTES = 1024

#: One frozen injection mode per daemon_outcome.
FAILURE_INJECTIONS: Mapping[str, str] = {
    outcome: f"inject-{outcome}" for outcome in DAEMON_OUTCOMES
}


def validate_daemon_outcome(outcome: Any) -> str:
    if not isinstance(outcome, str) or outcome not in DAEMON_OUTCOMES:
        raise ContractError(
            f"unknown daemon_outcome {outcome!r}; expected one of {sorted(DAEMON_OUTCOMES)!r}"
        )
    return outcome


def validate_client_protocol_relation(relation: Any) -> str:
    if not isinstance(relation, str) or relation not in CLIENT_PROTOCOL_RELATIONS:
        raise ContractError(
            f"unknown client_protocol_relation {relation!r}; expected one of "
            f"{sorted(CLIENT_PROTOCOL_RELATIONS)!r}"
        )
    return relation


def protocol_acceptance(relation: str) -> bool:
    """exact/compatible negotiate; incompatible/unknown_capability fail closed."""
    validate_client_protocol_relation(relation)
    return relation in {"exact", "compatible"}


@dataclass(frozen=True)
class FixturePlan:
    """One deterministic owner-lifecycle CasePlan (daemon_outcome x protocol relation)."""

    case_id: str
    ordinal: int
    daemon_outcome: str
    client_protocol_relation: str

    def parameters(self) -> Dict[str, str]:
        return {
            "daemon_outcome": self.daemon_outcome,
            "client_protocol_relation": self.client_protocol_relation,
        }


def expand_owner_lifecycle_plans() -> Tuple[FixturePlan, ...]:
    """The six outcomes x four relations = 24 successor CasePlans, deterministic order."""
    return tuple(
        FixturePlan(
            case_id=f"owner-lifecycle.daemon-outcome.{ordinal:04d}",
            ordinal=ordinal,
            daemon_outcome=outcome,
            client_protocol_relation=relation,
        )
        for ordinal, (outcome, relation) in enumerate(
            product(DAEMON_OUTCOMES, CLIENT_PROTOCOL_RELATIONS), start=1
        )
    )


def injectable_outcomes() -> Tuple[str, ...]:
    return tuple(FAILURE_INJECTIONS[outcome] for outcome in DAEMON_OUTCOMES)


OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR: Mapping[str, Any] = {
    "fixture_id": OWNER_LIFECYCLE_FIXTURE_ID,
    "schema_version": OWNER_LIFECYCLE_FIXTURE_SCHEMA,
    "execution_provenance": BENCHMARK_FIXTURE,
    "same_process": True,
    "daemon_processes": 1,
    "device_executor": "fake",
    "real_backend_interface": False,
    "separate_mlx_worker": False,
    "claimable": False,
}

#: Known benchmark fixture descriptors; the authoritative fixture ID registry.
FIXTURE_DESCRIPTORS: Mapping[str, Mapping[str, Any]] = {
    OWNER_LIFECYCLE_FIXTURE_ID: OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR,
}

#: Active fixture selection seam: maps a selected lane to a benchmark fixture
#: ID. PR 4 activates the owner-lifecycle lane through this seam under the
#: already-active absorbing fixture-taint interlock; selecting the fixture
#: taints the run before any case START and makes it not_claimable_fixture.
#: Tests patch this mapping to prove the interlock end to end.
FIXTURE_SELECTION_SEAM: Dict[str, str] = {
    "protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID,
}


def known_fixture_ids() -> Tuple[str, ...]:
    return tuple(sorted(FIXTURE_DESCRIPTORS))


def fixture_descriptor(fixture_id: str) -> Optional[Mapping[str, Any]]:
    return FIXTURE_DESCRIPTORS.get(fixture_id)


def describe() -> Mapping[str, Any]:
    return {
        "fixture": OWNER_LIFECYCLE_FIXTURE_ID,
        "schema_version": OWNER_LIFECYCLE_FIXTURE_SCHEMA,
        "same_process": True,
        "daemon_processes": 1,
        "device_executor": "fake",
        "real_backend_interface": False,
        "separate_mlx_worker": False,
        "claimable": False,
        "daemon_outcomes": list(DAEMON_OUTCOMES),
        "client_protocol_relations": list(CLIENT_PROTOCOL_RELATIONS),
        "plan_count": len(expand_owner_lifecycle_plans()),
    }


class DeviceExecutorFixture:
    """One in-process daemon process containing a fake Device Executor.

    Same-process by construction: no subprocess, no socket, no separate MLX
    Worker, and no real Backend Interface claim. The six frozen injections are
    applied deterministically from ``daemon_outcome``; the client protocol
    relation decides whether negotiation succeeds. Every trajectory fails
    closed: no backend call before initialization, no successful receipt after
    daemon loss, at most one device owner, bounded transport frames, and no
    turn before successful negotiation.
    """

    def __init__(self, daemon_outcome: str, client_protocol_relation: str) -> None:
        self.daemon_outcome = validate_daemon_outcome(daemon_outcome)
        self.client_protocol_relation = validate_client_protocol_relation(
            client_protocol_relation
        )
        self._accepted = protocol_acceptance(self.client_protocol_relation)
        self._state = "initializing"
        self._initialized = False
        self._daemon_lost = False
        self._backend_calls = 0
        self._backend_calls_before_initialization = 0
        self._turns_started = 0
        self._bootstrap_trace: List[Dict[str, Any]] = []
        self._client_transport_trace: List[Dict[str, Any]] = []
        self._process_trace: List[Dict[str, Any]] = []
        self._turn_receipts: List[Dict[str, Any]] = []
        self._latency_samples: List[int] = []
        self._simulate()

    # -- deterministic trajectory -------------------------------------------------

    def _backend_call(self) -> None:
        self._backend_calls += 1
        if not self._initialized:
            self._backend_calls_before_initialization += 1
        self._bootstrap_trace.append(
            {
                "event": "backend_call",
                "before_initialization": not self._initialized,
            }
        )

    def _accept_client_frame(self, frame_bytes: int) -> None:
        self._client_transport_trace.append(
            {
                "event": "client_frame",
                "accepted": True,
                "malformed": False,
                "frame_bytes": frame_bytes,
                "max_frame_bytes": MAX_CLIENT_FRAME_BYTES,
            }
        )

    def _turn(self, *, commit: bool, after_loss: bool, outcome: str) -> None:
        self._turns_started += 1
        self._turn_receipts.append(
            {
                "turn_id": self._turns_started,
                "started": True,
                "committed": commit,
                "after_daemon_loss": after_loss,
                "outcome": outcome,
            }
        )

    def _simulate(self) -> None:
        self._bootstrap_trace.append(
            {
                "event": "daemon_start",
                "same_process": True,
                "daemon_processes": 1,
            }
        )
        self._client_transport_trace.append(
            {
                "event": "client_handshake",
                "client_protocol_relation": self.client_protocol_relation,
                "accepted": self._accepted,
            }
        )
        if not self._accepted:
            self._state = "protocol_rejected"
            self._bootstrap_trace.append(
                {
                    "event": "protocol_negotiation_rejected",
                    "client_protocol_relation": self.client_protocol_relation,
                }
            )
            self._process_trace.append(
                {
                    "event": "daemon_failed_closed",
                    "reason": "client_protocol_not_negotiated",
                    "before_backend_initialization": True,
                }
            )
            return
        if self.daemon_outcome == "failure_before_backend_initialization":
            self._state = "daemon_lost"
            self._daemon_lost = True
            self._bootstrap_trace.append(
                {
                    "event": "device_executor_initialization_failed",
                    "backend_calls_before_initialization": 0,
                }
            )
            self._process_trace.append(
                {
                    "event": "daemon_lost",
                    "phase": "before_backend_initialization",
                    "outcome": self.daemon_outcome,
                }
            )
            return
        self._state = "ready"
        self._initialized = True
        self._bootstrap_trace.append(
            {
                "event": "device_executor_ready",
                "fake_device_executor": True,
                "backend_interface_claimed": False,
                "owner_count": 1,
            }
        )
        if self.daemon_outcome == "malformed_client_frame":
            self._state = "failed_closed"
            self._client_transport_trace.append(
                {
                    "event": "client_frame",
                    "accepted": False,
                    "malformed": True,
                    "frame_bytes": MAX_CLIENT_FRAME_BYTES + 1,
                    "max_frame_bytes": MAX_CLIENT_FRAME_BYTES,
                }
            )
            self._process_trace.append(
                {
                    "event": "daemon_failed_closed",
                    "reason": "malformed_client_frame_rejected_before_execution",
                }
            )
            return
        if self.daemon_outcome == "duplicate_client_command":
            self._accept_client_frame(512)
            self._client_transport_trace.append(
                {
                    "event": "client_command",
                    "command_sequence": 1,
                    "duplicate": False,
                    "accepted": True,
                }
            )
            self._client_transport_trace.append(
                {
                    "event": "client_command",
                    "command_sequence": 1,
                    "duplicate": True,
                    "accepted": False,
                    "rejected_reason": "duplicate_client_command_at_most_once",
                }
            )
            self._backend_call()
            self._turn(commit=True, after_loss=False, outcome="completed")
            self._latency_samples = [100]
            self._state = "turn_completed"
            return
        if self.daemon_outcome == "failure_during_turn":
            self._accept_client_frame(512)
            self._backend_call()
            self._state = "daemon_lost"
            self._daemon_lost = True
            self._turn(commit=False, after_loss=True, outcome="indeterminate")
            self._process_trace.append(
                {
                    "event": "daemon_lost",
                    "phase": "during_turn",
                    "outcome": self.daemon_outcome,
                }
            )
            return
        if self.daemon_outcome == "safe_point_timeout":
            self._accept_client_frame(512)
            self._backend_call()
            self._state = "timed_out_failed_closed"
            self._turn(commit=False, after_loss=False, outcome="safe_point_timeout")
            self._process_trace.append(
                {
                    "event": "daemon_timeout",
                    "phase": "safe_point",
                    "outcome": self.daemon_outcome,
                }
            )
            return
        # normal: one completed turn, committed receipt, bounded latency samples.
        self._accept_client_frame(512)
        self._backend_call()
        self._turn(commit=True, after_loss=False, outcome="completed")
        self._latency_samples = [100, 110, 120]
        self._state = "turn_completed"

    # -- successor lane fixture operations ----------------------------------------

    def launch_fixture_daemon(self) -> Dict[str, Any]:
        return {
            "operation": "launch-fixture-daemon",
            "fixture_id": OWNER_LIFECYCLE_FIXTURE_ID,
            "daemon_state": self._state,
            "same_process": True,
            "real_backend_interface": False,
            "separate_mlx_worker": False,
            "bootstrap_trace": list(self._bootstrap_trace),
        }

    def inject_daemon_outcome(self, outcome: str) -> Dict[str, Any]:
        """Apply the frozen failure injection; a mismatched injection fails closed."""
        validate_daemon_outcome(outcome)
        if outcome != self.daemon_outcome:
            raise ContractError(
                f"daemon injection mismatch: fixture plans {self.daemon_outcome!r} "
                f"but received {outcome!r}"
            )
        return {
            "operation": "inject-daemon-outcome",
            "fixture_id": OWNER_LIFECYCLE_FIXTURE_ID,
            "outcome": self.daemon_outcome,
            "injection": FAILURE_INJECTIONS[self.daemon_outcome],
            "frozen": True,
            "deterministic": True,
        }

    def inspect_daemon_fail_closed(self) -> Dict[str, Any]:
        metrics = self.observed_metrics()
        failures = self._fail_closed_failures(metrics)
        return {
            "operation": "inspect-daemon-fail-closed",
            "fixture_id": OWNER_LIFECYCLE_FIXTURE_ID,
            "daemon_outcome": self.daemon_outcome,
            "client_protocol_relation": self.client_protocol_relation,
            "daemon_state": self._state,
            "same_process": True,
            "daemon_processes": 1,
            "device_executor": "fake",
            "real_backend_interface": False,
            "separate_mlx_worker": False,
            "metrics": metrics,
            "fail_closed": not failures,
            "failures": failures,
            "evidence": {
                "bootstrap_trace": list(self._bootstrap_trace),
                "client_transport_trace": list(self._client_transport_trace),
                "process_trace": list(self._process_trace),
                "turn_receipts": list(self._turn_receipts),
            },
        }

    # -- successor gate metrics and fail-closed invariants ------------------------

    def observed_metrics(self) -> Dict[str, Any]:
        committed_after_loss = sum(
            1
            for receipt in self._turn_receipts
            if receipt["committed"] and receipt["after_daemon_loss"]
        )
        accepted_frames = [
            int(item["frame_bytes"])
            for item in self._client_transport_trace
            if item.get("accepted") and "frame_bytes" in item
        ]
        return {
            "simultaneous_device_owner_count": 1 if self._initialized else 0,
            "backend_calls_before_initialization_count": self._backend_calls_before_initialization,
            "successful_receipt_after_daemon_loss_count": committed_after_loss,
            "client_transport_max_frame_bytes": max(accepted_frames, default=0),
            "client_transport_latency_samples_us": list(self._latency_samples),
        }

    def _fail_closed_failures(self, metrics: Mapping[str, Any]) -> List[str]:
        failures: List[str] = []
        if metrics["simultaneous_device_owner_count"] > 1:
            failures.append("simultaneous_device_owner_count exceeds 1")
        if metrics["backend_calls_before_initialization_count"] != 0:
            failures.append("backend call before fake executor initialization")
        if metrics["successful_receipt_after_daemon_loss_count"] != 0:
            failures.append("successful receipt after daemon loss")
        if metrics["client_transport_max_frame_bytes"] > MAX_CLIENT_FRAME_BYTES:
            failures.append("client transport frame exceeds fixture bound")
        if self._turns_started and not self._accepted:
            failures.append("turn started without successful protocol negotiation")
        return failures

    def evidence(self) -> Mapping[str, Any]:
        return {
            "bootstrap_trace": list(self._bootstrap_trace),
            "client_transport_trace": list(self._client_transport_trace),
            "process_trace": list(self._process_trace),
            "turn_receipts": list(self._turn_receipts),
        }

    def trajectory_signature(self) -> str:
        """Deterministic identity of the complete fixture trajectory."""
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(self.evidence(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
