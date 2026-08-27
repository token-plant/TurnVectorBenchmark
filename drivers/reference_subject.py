#!/usr/bin/env python3
"""Non-claimable SubjectAdapter v1 fixture for benchmark judge self-tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from reference_driver import ReferenceScheduler


SUBJECT_PROTOCOL = "turnvector.benchmark.subject.v1"
BUILD_IDENTITY = "reference-fixture-build-v1"

LANE_PROTOCOLS = {
    "core-event-replay": "turnvector.benchmark.core-replay.v1",
    "scheduler-policy": "turnvector.benchmark.scheduler-policy.v1",
    "scheduler-performance": "turnvector.benchmark.scheduler-performance.v1",
    "request-serving-lifecycle": "turnvector.benchmark.serving.v1",
    "mlx-native-correctness": "turnvector.benchmark.mlx-native.v1",
    "bounded-turn-and-ffi": "turnvector.benchmark.mlx-turn.v1",
    "residency-and-memory-governor": "turnvector.benchmark.memory-governor.v1",
    "cross-model-serving": "turnvector.benchmark.cross-model-serving.v1",
    "observability-qualification": "turnvector.benchmark.observability.v1",
    "persistence-and-recovery": "turnvector.benchmark.persistence.v1",
    "protocol-and-owner-lifecycle": "turnvector.benchmark.owner-lifecycle.v1",
    "certification-envelopes": "turnvector.benchmark.certification.v1",
}

REQUIRED_RAW_ARTIFACTS = {
    "core-event-replay": ["event_trace", "state_hashes", "invariant_results"],
    "scheduler-policy": [],
    "scheduler-performance": ["plan_trace", "oracle_trace", "latency_samples", "measurement_trace"],
    "request-serving-lifecycle": ["request_trace", "status_trace", "output_trace", "capacity_trace"],
    "mlx-native-correctness": ["native_oracle", "output_hashes", "logits_hashes", "kv_hashes", "owner_thread_trace"],
    "bounded-turn-and-ffi": ["native_oracle", "turn_receipts", "latency_samples", "cleanup_trace"],
    "residency-and-memory-governor": ["governor_decisions", "resource_trace", "reservation_trace", "residency_receipts", "memory_samples"],
    "cross-model-serving": ["workload", "serving_trace", "turn_receipts", "latency_samples", "output_hashes"],
    "observability-qualification": ["command_buffer_trace", "turn_receipts", "instruments_trace", "calibration_pairs", "latency_samples", "throughput_samples"],
    "persistence-and-recovery": ["snapshot_manifest", "fault_trace", "control_trace", "audit_trace", "recovery_trace", "output_hashes", "kv_hashes"],
    "protocol-and-owner-lifecycle": ["bootstrap_trace", "client_transport_trace", "process_trace", "turn_receipts"],
    "certification-envelopes": ["certification_records", "admission_trace", "turn_receipts", "quarantine_trace"],
}

def emit(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixtureSubject:
    def __init__(
        self,
        fail_gate: Optional[str],
        omit_artifact: Optional[str],
        escape_artifact: bool,
        malformed_hello: bool,
        unsupported_current: bool,
        timeout_kind: Optional[str],
    ) -> None:
        self.fail_gate = fail_gate
        self.omit_artifact = omit_artifact
        self.escape_artifact = escape_artifact
        self.malformed_hello = malformed_hello
        self.unsupported_current = unsupported_current
        self.timeout_kind = timeout_kind
        self.lane_id: Optional[str] = None
        self.case_id: Optional[str] = None
        self.case_ordinal = 0
        self.case_parameters: Dict[str, Any] = {}
        self.generic_record_emitted = False
        self.artifact_root: Optional[Path] = None
        self.case_directory: Optional[str] = None
        self.scheduler: Optional[ReferenceScheduler] = None
        self.scheduler_repetition = 0
        self.scheduler_schedule_index = 0
        self.artifacts_emitted = False

    def hello(self, message: Mapping[str, Any]) -> None:
        if self.timeout_kind == "hello":
            time.sleep(60)
        if message.get("protocol_version") != SUBJECT_PROTOCOL:
            raise ValueError("unsupported SubjectAdapter protocol")
        lane_id = message.get("lane_id")
        if lane_id not in LANE_PROTOCOLS:
            raise ValueError("unknown lane")
        self.lane_id = str(lane_id)
        source = Path(__file__).resolve()
        response = {
                "kind": "hello_ack",
                "protocol_version": SUBJECT_PROTOCOL,
                "subject": {
                    "name": "reference-subject-fixture",
                    "version": "1.0.0",
                    "kind": "fixture",
                    "build_identity": BUILD_IDENTITY,
                },
                "supported_lanes": {
                    key: value
                    for key, value in LANE_PROTOCOLS.items()
                    if not self.unsupported_current or key != self.lane_id
                },
                "binary_manifest": [{"path": str(source), "sha256": sha256(source)}],
                "dependency_manifest": [],
                "environment_identity": {
                    "device_class": "fixture",
                    "memory_bytes": 1048576,
                    "os_build": "fixture",
                },
            }
        if self.malformed_hello:
            response["unexpected"] = True
        emit(response)

    def case_open(self, message: Mapping[str, Any]) -> None:
        case = message["case"]
        self.case_id = case["case_id"]
        self.case_ordinal = case["ordinal"]
        self.case_parameters = dict(case["parameters"])
        self.generic_record_emitted = False
        self.artifact_root = Path(message["artifact_root"])
        self.case_directory = message["case_directory"]
        emit({"kind": "case_open_ack", "case_id": self.case_id, "status": "ready"})

    def case_step(self, message: Mapping[str, Any]) -> None:
        if self.timeout_kind == "case_step":
            time.sleep(60)
        operation = message["operation"]
        payload = message["payload"]
        evidence: Mapping[str, Any]
        if operation == "scheduler_initialize":
            self.scheduler = ReferenceScheduler(payload["models"])
            self.scheduler_repetition = payload["repetition"]
            self.scheduler_schedule_index = 0
            evidence = {"model_ledgers_us": self.scheduler.ledger_map()}
        elif operation == "scheduler_schedule":
            if self.scheduler is None:
                raise ValueError("scheduler is not initialized")
            self.scheduler_schedule_index += 1
            response = self.scheduler.schedule(payload)
            evidence = {
                "candidate_id": response["candidate_id"],
                "runnable_ledgers_us": response["runnable_ledgers_us"],
            }
            if self._fail_scheduler_selection():
                alternatives = [
                    item["candidate_id"]
                    for item in payload["candidates"]
                    if item["candidate_id"] != evidence["candidate_id"]
                ]
                evidence = dict(evidence)
                evidence["candidate_id"] = alternatives[0] if alternatives else "fixture-wrong-plan"
        elif operation == "scheduler_receipt":
            if self.scheduler is None:
                raise ValueError("scheduler is not initialized")
            response = self.scheduler.receipt(payload)
            ledgers = dict(response["model_ledgers_us"])
            if self.fail_gate == "scheduler-policy.receipt-accounting":
                model_id = sorted(ledgers)[0]
                ledgers[model_id] = "1/1" if ledgers[model_id] != "1/1" else "2/1"
            evidence = {"model_ledgers_us": ledgers}
        else:
            if not self.generic_record_emitted:
                evidence = {"record": self._case_record()}
                self.generic_record_emitted = True
            else:
                evidence = {"operation": operation, "fixture_recorded": True}
        emit(
            {
                "kind": "case_step_ack",
                "case_id": message["case_id"],
                "step_index": message["step_index"],
                "evidence": evidence,
            }
        )

    def _fail_scheduler_selection(self) -> bool:
        if self.scheduler_schedule_index != 1:
            return False
        if self.fail_gate == "scheduler-policy.oracle-selection":
            return True
        return (
            self.fail_gate == "scheduler-policy.deterministic-replay"
            and self.scheduler_repetition == 1
        )

    def _case_record(self) -> Dict[str, Any]:
        assert self.lane_id is not None
        lane = self.lane_id
        if lane == "core-event-replay":
            record: Dict[str, Any] = {
                "first_state_hash": "state-1",
                "replay_state_hash": "state-1",
                "invariants": [True],
                "transition_committed": self.case_parameters.get("result") != "failed",
                "effects": [],
            }
        elif lane == "scheduler-performance":
            record = {
                "plan_pairs": [{"expected": "candidate-a", "observed": "candidate-a"}],
                "decision_latency_us": [10, 11, 12],
                "decision_work": {"operations": 10000, "seconds": 0.01},
                "driver_ipc_included": False,
            }
        elif lane == "request-serving-lifecycle":
            client_event = self.case_parameters.get("client_event")
            cancellation_sequence = None
            receipt_sequence = 10
            disconnect_observed = False
            backpressure_observed = False
            if client_event == "cancel_before_receipt":
                cancellation_sequence = 9
            elif client_event == "cancel_after_receipt":
                cancellation_sequence = 11
            elif client_event == "disconnect":
                cancellation_sequence = 10
                receipt_sequence = None
                disconnect_observed = True
            elif client_event == "backpressure_timeout":
                cancellation_sequence = 10
                receipt_sequence = None
                disconnect_observed = True
                backpressure_observed = True
            record = {
                "lifecycle": [
                    "accepted",
                    "preparing",
                    "admitted",
                    "materialized",
                    "queued",
                    "terminal",
                ],
                "outputs": [
                    {"publication_id": "publication-1", "sequence": 0, "reserved": True}
                ],
                "client_event": client_event,
                "acceptance_reserved": False,
                "acceptance_backend_handle": False,
                "cancellation_commit_sequence": cancellation_sequence,
                "receipt_commit_sequence": receipt_sequence,
                "disconnect_observed": disconnect_observed,
                "backpressure_timeout_observed": backpressure_observed,
                "terminal_status_emitted": True,
            }
        elif lane == "mlx-native-correctness":
            record = {
                "parity": [
                    {"kind": "output", "expected_sha256": "same", "observed_sha256": "same"},
                    {"kind": "logits", "expected_sha256": "same", "observed_sha256": "same"},
                    {"kind": "kv", "expected_sha256": "same", "observed_sha256": "same"},
                ],
                "owner_thread_id": 1,
                "execution_thread_ids": [1],
                "cross_thread_rejected": True,
            }
        elif lane == "bounded-turn-and-ffi":
            record = {
                "turn_service_us": [100, 110, 120],
                "ffi_pairs": [{"expected": "same", "observed": "same"}],
                "ffi_baseline_us": [100, 100, 100],
                "ffi_candidate_us": [100, 100, 100],
                "native_outcomes": [{"terminal": "completed", "cleaned": True}],
            }
        elif lane == "residency-and-memory-governor":
            record = {
                "governor_decisions": [{
                    "resource_safe": True,
                    "admitted": True,
                    "observed_resource_mode": self.case_parameters.get("resource_mode"),
                    "safety_filter_sequence": 1,
                    "schedule_sequence": 2,
                }],
                "lease_checks": [{
                    "busy": True,
                    "loading": False,
                    "lease_count": 1,
                    "selected_for_unload": False,
                }],
                "reservations": [
                    {"reserved": 1024, "allocated": 512, "pending_reclaim": 0, "released": 512}
                ],
                "pending_reclaim": [{
                    "process_reclaimed": False,
                    "backend_reclaimed": False,
                    "reservation_held": True,
                }],
                "process_samples_bytes": [1024],
                "reclaim_us": [100, 110, 120],
            }
        elif lane == "cross-model-serving":
            weight_relation = self.case_parameters.get("model_weights")
            beta_weight = 3 if weight_relation == "1_to_3" else 1
            record = {
                "progress_us": [10, 11],
                "timing_us": [10, 11],
                "receipts": [
                    {"model": "alpha", "engine_service_us": 100, "weight": 1},
                    {
                        "model": "beta",
                        "engine_service_us": 100 * beta_weight,
                        "weight": beta_weight,
                    },
                ],
                "latency_samples": [
                    {"ttft_us": 100, "tpot_us": 10},
                    {"ttft_us": 110, "tpot_us": 11},
                ],
                "throughput": {"tokens": 1000, "seconds": 0.1},
                "outputs": [{"expected": "same", "observed": "same"}],
            }
        elif lane == "observability-qualification":
            record = {
                "instruments_capture_present": True,
                "turn_ids": [1, 2],
                "command_buffers": [
                    {"turn_id": 1, "gpu_start_ns": 100, "gpu_end_ns": 200, "status": "completed"},
                    {"turn_id": 2, "gpu_start_ns": 300, "gpu_end_ns": 400, "status": "completed"},
                ],
                "instruments_pairs": [
                    {"measured_us": 100, "instruments_us": 100},
                    {"measured_us": 100, "instruments_us": 100},
                ],
                "telemetry": {
                    "off_throughput": 100,
                    "on_throughput": 100,
                    "off_tpot_p95_us": 10,
                    "on_tpot_p95_us": 10,
                },
            }
        elif lane == "persistence-and-recovery":
            record = {
                "snapshots": [{"expected": "same", "observed": "same"}],
                "invalid_restore_attempts": [{"valid": False, "restored": False}],
                "authorities": [{"expected": "same", "observed": "same"}],
                "effects": [{"id": "effect-1", "executions": 1}],
            }
        elif lane == "protocol-and-owner-lifecycle":
            outcome = self.case_parameters.get("daemon_outcome")
            relation = self.case_parameters.get("client_protocol_relation")
            accepted = relation in {"exact", "compatible"}
            initialized = accepted and outcome != "failure_before_backend_initialization"
            accepted_frames = []
            if accepted and outcome in {
                "normal",
                "duplicate_client_command",
                "failure_during_turn",
                "safe_point_timeout",
            }:
                accepted_frames = (
                    [128, 256]
                    if outcome in {"normal", "duplicate_client_command"}
                    else [128]
                )
            record = {
                "daemon": {
                    "owners": ["daemon-1"] if initialized else [],
                    "backend_calls_before_initialization": 0,
                    "successful_receipts_after_daemon_loss": 0,
                },
                "client_transport": {
                    "frame_bytes": accepted_frames,
                    "latency_us": [10, 12],
                },
            }
        elif lane == "certification-envelopes":
            applicable = (
                self.case_parameters.get("changed_identity") == "none"
                and self.case_parameters.get("record_state") == "applicable"
            )
            record = {
                "admissions": [
                    {
                        "observed_record_applicable": applicable,
                        "shared_turn_started": applicable,
                    }
                ],
                "scope_uses": [{"certified": "scope-1", "used": "scope-1"}],
                "quarantine": {
                    "bound_violation_sequence": 10,
                    "turn_start_sequences": [],
                },
                "recertification": {
                    "required": not applicable,
                    "completed": not applicable,
                    "new_record_applied": not applicable,
                    "shared_turn_started_after": not applicable,
                },
            }
        else:
            raise ValueError(f"no reference raw record for lane {lane!r}")
        self._inject_raw_failure(record)
        return record

    def _inject_raw_failure(self, record: Dict[str, Any]) -> None:
        gate = self.fail_gate
        if gate is None or self.lane_id is None or not gate.startswith(f"{self.lane_id}."):
            return
        if gate == "core-event-replay.replay-hash":
            record["replay_state_hash"] = "different"
        elif gate == "core-event-replay.invariant-safety":
            record["transition_committed"] = True
            record["invariants"] = [False]
        elif gate == "core-event-replay.failed-transition-effects":
            record["transition_committed"] = False
            record["effects"] = ["leaked-effect"]
        elif gate == "scheduler-performance.oracle-equivalence":
            record["plan_pairs"][0]["observed"] = "different"
        elif gate == "scheduler-performance.decision-latency":
            record["decision_latency_us"] = [1e18]
        elif gate == "scheduler-performance.decision-throughput":
            record["decision_work"] = {"operations": 0, "seconds": 1}
        elif gate == "scheduler-performance.measurement-boundary":
            record["driver_ipc_included"] = True
        elif gate == "request-serving-lifecycle.lifecycle-order":
            record["lifecycle"] = ["accepted", "materialized", "admitted", "terminal"]
        elif gate == "request-serving-lifecycle.output-at-most-once":
            record["outputs"].append(dict(record["outputs"][0]))
        elif gate == "request-serving-lifecycle.capacity-bound":
            record["outputs"][0]["reserved"] = False
        elif gate == "request-serving-lifecycle.terminal-status":
            record["terminal_status_emitted"] = False
        elif gate == "request-serving-lifecycle.client-event-semantics":
            event = record["client_event"]
            if event == "none":
                record["disconnect_observed"] = True
            elif event == "cancel_before_receipt":
                record["cancellation_commit_sequence"] = 11
            elif event == "cancel_after_receipt":
                record["cancellation_commit_sequence"] = None
            elif event == "disconnect":
                record["disconnect_observed"] = False
            else:
                record["backpressure_timeout_observed"] = False
        elif gate == "mlx-native-correctness.output-parity":
            record["parity"][0]["observed_sha256"] = "different"
        elif gate == "mlx-native-correctness.logits-parity":
            record["parity"][1]["observed_sha256"] = "different"
        elif gate == "mlx-native-correctness.kv-parity":
            record["parity"][2]["observed_sha256"] = "different"
        elif gate == "mlx-native-correctness.owner-violation":
            record["execution_thread_ids"] = [2]
        elif gate == "bounded-turn-and-ffi.turn-bound":
            record["turn_service_us"] = [1e18]
        elif gate == "bounded-turn-and-ffi.ffi-output-parity":
            record["ffi_pairs"][0]["observed"] = "different"
        elif gate == "bounded-turn-and-ffi.ffi-latency":
            record["ffi_candidate_us"] = [200, 200, 200]
        elif gate == "bounded-turn-and-ffi.cleanup":
            record["native_outcomes"][0]["cleaned"] = False
        elif gate == "residency-and-memory-governor.unsafe-admission":
            record["governor_decisions"][0]["resource_safe"] = False
        elif gate == "residency-and-memory-governor.lease-safe-unload":
            record["lease_checks"][0]["selected_for_unload"] = True
        elif gate == "residency-and-memory-governor.resource-mode-order":
            record["governor_decisions"][0]["safety_filter_sequence"] = 3
        elif gate == "residency-and-memory-governor.reservation-conservation":
            record["reservations"][0]["released"] = 511
        elif gate == "residency-and-memory-governor.pending-reclaim-retention":
            record["pending_reclaim"][0]["reservation_held"] = False
        elif gate == "residency-and-memory-governor.peak-memory-bound":
            record["process_samples_bytes"] = [10**18]
        elif gate == "residency-and-memory-governor.reclaim-bound":
            record["reclaim_us"] = [1e18]
        elif gate == "cross-model-serving.progress-bound":
            record["progress_us"] = [1e18]
        elif gate == "cross-model-serving.timing-commitment":
            record["timing_us"] = [1e18]
        elif gate == "cross-model-serving.service-share":
            record["receipts"][1]["engine_service_us"] *= 2
        elif gate == "cross-model-serving.ttft-bound":
            record["latency_samples"][0]["ttft_us"] = 1e18
        elif gate == "cross-model-serving.tpot-bound":
            record["latency_samples"][0]["tpot_us"] = 1e18
        elif gate == "cross-model-serving.throughput-bound":
            record["throughput"] = {"tokens": 0, "seconds": 1}
        elif gate == "cross-model-serving.output-correctness":
            record["outputs"][0]["observed"] = "different"
        elif gate == "observability-qualification.instruments-capture":
            record["instruments_capture_present"] = False
        elif gate == "observability-qualification.timestamp-coverage":
            record["command_buffers"][0]["gpu_start_ns"] = 0
        elif gate == "observability-qualification.attribution-errors":
            record["command_buffers"][0]["turn_id"] = 999
        elif gate in {
            "observability-qualification.calibration-median",
            "observability-qualification.calibration-p95",
        }:
            record["instruments_pairs"] = [
                {"measured_us": 200, "instruments_us": 100},
                {"measured_us": 200, "instruments_us": 100},
            ]
        elif gate == "observability-qualification.telemetry-throughput":
            record["telemetry"]["on_throughput"] = 50
        elif gate == "observability-qualification.telemetry-tpot":
            record["telemetry"]["on_tpot_p95_us"] = 20
        elif gate == "persistence-and-recovery.snapshot-parity":
            record["snapshots"][0]["observed"] = "different"
        elif gate == "persistence-and-recovery.invalid-snapshot-use":
            record["invalid_restore_attempts"][0]["restored"] = True
        elif gate == "persistence-and-recovery.authority-divergence":
            record["authorities"][0]["observed"] = "different"
        elif gate == "persistence-and-recovery.effect-replay":
            record["effects"][0]["executions"] = 2
        elif gate == "protocol-and-owner-lifecycle.multiple-owner":
            record["daemon"]["owners"] = ["daemon-1", "daemon-2"]
        elif gate == "protocol-and-owner-lifecycle.unnegotiated-execution":
            record["daemon"]["backend_calls_before_initialization"] = 1
        elif gate == "protocol-and-owner-lifecycle.fabricated-receipt":
            record["daemon"]["successful_receipts_after_daemon_loss"] = 1
        elif gate == "protocol-and-owner-lifecycle.bound-compliance":
            record["client_transport"]["frame_bytes"] = [1e18]
        elif gate == "protocol-and-owner-lifecycle.transport-latency":
            record["client_transport"]["latency_us"] = [1e18]
        elif gate == "certification-envelopes.applicability-exact":
            record["admissions"][0]["observed_record_applicable"] = not record[
                "admissions"
            ][0]["observed_record_applicable"]
        elif gate == "certification-envelopes.uncertified-start":
            record["admissions"][0]["shared_turn_started"] = True
        elif gate == "certification-envelopes.scope-leak":
            record["scope_uses"][0]["used"] = "other-scope"
        elif gate == "certification-envelopes.quarantine-delay":
            record["quarantine"]["turn_start_sequences"] = [11]
        elif gate == "certification-envelopes.explicit-recertification":
            record["recertification"]["new_record_applied"] = False
        else:
            raise ValueError(f"no raw failure fixture for gate {gate!r}")

    def case_close(self, message: Mapping[str, Any]) -> None:
        assert self.lane_id is not None
        artifacts = self._artifacts()
        emit(
            {
                "kind": "case_close_ack",
                "case_id": message["case_id"],
                "observations": {},
                "artifacts": artifacts,
            }
        )

    def _artifacts(self) -> List[Mapping[str, Any]]:
        if self.artifacts_emitted:
            return []
        self.artifacts_emitted = True
        assert self.lane_id is not None
        assert self.artifact_root is not None
        assert self.case_directory is not None
        descriptors: List[Mapping[str, Any]] = []
        for artifact_id in REQUIRED_RAW_ARTIFACTS[self.lane_id]:
            if artifact_id == self.omit_artifact:
                continue
            relative = Path(self.case_directory) / f"{artifact_id}.jsonl"
            path = self.artifact_root / relative
            path.write_text(
                json.dumps(
                    {
                        "fixture": True,
                        "lane_id": self.lane_id,
                        "artifact_id": artifact_id,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            descriptors.append(
                {
                    "id": artifact_id,
                    "path": relative.as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        if self.escape_artifact:
            descriptors.append(
                {
                    "id": "escape",
                    "path": "../escape.txt",
                    "size": 0,
                    "sha256": "0" * 64,
                }
            )
        return descriptors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-gate")
    parser.add_argument("--omit-artifact")
    parser.add_argument("--escape-artifact", action="store_true")
    parser.add_argument("--malformed-hello", action="store_true")
    parser.add_argument("--unsupported-current", action="store_true")
    parser.add_argument("--timeout-kind", choices=["hello", "case_step"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    subject = FixtureSubject(
        args.fail_gate,
        args.omit_artifact,
        args.escape_artifact,
        args.malformed_hello,
        args.unsupported_current,
        args.timeout_kind,
    )
    for line in sys.stdin:
        try:
            message = json.loads(line)
            kind = message.get("kind")
            if kind == "hello":
                subject.hello(message)
            elif kind == "case_open":
                subject.case_open(message)
            elif kind == "case_step":
                subject.case_step(message)
            elif kind == "case_close":
                subject.case_close(message)
            elif kind == "shutdown":
                emit({"kind": "shutdown_ack"})
                return 0
            else:
                raise ValueError(f"unsupported message kind {kind!r}")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(f"reference subject contract error: {error}", file=sys.stderr, flush=True)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
