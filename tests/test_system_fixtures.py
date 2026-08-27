from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.workers.worker_proxy import injected_frame, pop_u32be_frames
from turnvector_benchmark.core import ContractError, canonical_json
from turnvector_benchmark.evidence import sha256_file
from turnvector_benchmark.lane_runner import (
    _benchmark_fixture_inputs,
    _core_event_case_input,
    _normalize_core_execution,
    _process_executable,
    _scheduler_performance_case_input,
    apply_persistence_fault,
)
from turnvector_benchmark.lane_contract import PlannedCase
from turnvector_benchmark.system_collectors import ProcessMemorySampler, XctraceCollector


ROOT = Path(__file__).resolve().parent.parent
WORKER_PROXY = ROOT / "fixtures" / "workers" / "worker_proxy.py"


class SystemFixtureTests(unittest.TestCase):
    def persistence_stage(
        self,
        *,
        process_id: int,
        fault_target: object = None,
        replacement: object = None,
        phase_marker: object = None,
    ) -> dict[str, object]:
        executable = _process_executable(process_id)
        assert executable is not None
        return {
            "process_id": process_id,
            "process_executable": str(executable),
            "process_sha256": sha256_file(executable),
            "fault_target": fault_target,
            "replacement": replacement,
            "phase_marker": phase_marker,
        }

    @staticmethod
    def staged_file(root: Path, name: str, role: str, value: bytes) -> dict[str, object]:
        path = root / name
        path.write_bytes(value)
        return {
            "path": name,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "role": role,
        }

    @staticmethod
    def allowed_python(process_id: int) -> dict[Path, str]:
        executable = _process_executable(process_id)
        assert executable is not None
        return {executable: sha256_file(executable)}

    def test_benchmark_process_sampler_reads_real_rss(self) -> None:
        sampler = ProcessMemorySampler([os.getpid()], interval_seconds=0.01)
        sampler.start()
        time.sleep(0.05)
        result = sampler.stop()
        self.assertTrue(result.process_samples)
        self.assertTrue(all(value > 0 for value in result.footprint_samples_bytes))
        self.assertIn("platform", result.system_pressure_before)
        self.assertIn("platform", result.system_pressure_after)

    def test_process_sampler_deduplicates_repeated_errors(self) -> None:
        sampler = ProcessMemorySampler([99_999_999], interval_seconds=0.001)
        sampler.start()
        time.sleep(0.05)
        result = sampler.stop()

        self.assertEqual(len(result.errors), 1)
        self.assertIn("99999999", result.errors[0])

    def test_scheduler_performance_inputs_have_benchmark_owned_exact_plans(self) -> None:
        snapshots, expected = _scheduler_performance_case_input(
            {
                "runnable_models": 8,
                "urgency_mix": "mixed",
                "resource_mode": "normal",
            }
        )
        self.assertEqual(len(snapshots), 4)
        self.assertEqual(len(expected), 4)
        self.assertTrue(all(item is not None for item in expected))
        self.assertTrue(
            all("/" in candidate["ledger"] for candidate in snapshots[0]["candidates"])
        )

        _, stopped = _scheduler_performance_case_input(
            {
                "runnable_models": 8,
                "urgency_mix": "none",
                "resource_mode": "stop_admission",
            }
        )
        self.assertEqual(stopped, [None, None, None, None])

    def test_core_replay_inputs_drive_independent_invariants(self) -> None:
        ordinal = 0
        for result in ("completed", "failed", "cancelled", "indeterminate"):
            for relation in (
                "current",
                "duplicate",
                "late",
                "unknown",
                "stale_generation",
            ):
                ordinal += 1
                case = PlannedCase(
                    case_id=f"core-event-replay.effect-result-replay.{ordinal:04d}",
                    lane_id="core-event-replay",
                    matrix_id="effect-result-replay",
                    ordinal=ordinal,
                    parameters={"result": result, "sequence_relation": relation},
                    behavior_case_ids=(
                        "deterministic-state",
                        "atomic-failure",
                        "effect-idempotence",
                        "cancellation-order",
                    ),
                    operations=("replay-event-stream", "replay-identical-stream"),
                    diagnostic_only=False,
                )
                case_input = _core_event_case_input(case)
                input_sha256 = hashlib.sha256(
                    canonical_json(case_input).encode("utf-8")
                ).hexdigest()
                should_apply = relation == "current"
                should_commit = should_apply and result != "failed"
                cancellation_expected = should_apply and result == "cancelled"
                execution = {
                    "input_sha256": input_sha256,
                    "final_state_sha256": "b" * 64 if should_commit else "a" * 64,
                    "receipt": {
                        "sequence_relation": relation,
                        "applied": should_apply,
                        "transition_committed": should_commit,
                        "state_before_sha256": "a" * 64,
                        "state_after_sha256": "b" * 64 if should_commit else "a" * 64,
                    },
                    "published_effects": (
                        [{"effect_id": "publication-1", "publication_sequence": 41}]
                        if should_commit and result == "completed"
                        else []
                    ),
                    "cancellation": {
                        "commit_sequence": 42 if cancellation_expected else None,
                        "terminal_sequence": 43 if cancellation_expected else None,
                    },
                }
                _, invariants = _normalize_core_execution(
                    execution,
                    case_input=case_input,
                    input_sha256=input_sha256,
                    where=case.case_id,
                )
                self.assertTrue(all(item["passed"] for item in invariants))

        duplicate_effect = dict(execution)
        duplicate_effect["published_effects"] = [
            {"effect_id": "publication-1", "publication_sequence": 41},
            {"effect_id": "publication-1", "publication_sequence": 42},
        ]
        _, invariants = _normalize_core_execution(
            duplicate_effect,
            case_input=case_input,
            input_sha256=input_sha256,
            where="duplicate-effect",
        )
        by_id = {item["id"]: item["passed"] for item in invariants}
        self.assertFalse(by_id["effect-idempotence"])

    def test_xctrace_fixture_exercises_capture_export_and_hash_custody(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        collector = XctraceCollector(
            ROOT / "fixtures" / "xctrace" / "fake_xctrace.py",
            os.getpid(),
            Path(temporary.name) / "capture",
        )
        collector.start()
        result = collector.stop()

        self.assertTrue(result["capture_present"])
        self.assertTrue(Path(result["trace_archive_path"]).is_file())
        self.assertTrue(Path(result["toc_path"]).is_file())
        self.assertEqual(len(result["trace_archive_sha256"]), 64)
        self.assertEqual(len(result["toc_sha256"]), 64)

    def test_worker_proxy_declares_normal_and_malicious_modes(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(WORKER_PROXY), "--mode", "normal", "--describe"],
            check=True,
            capture_output=True,
            text=True,
        )
        value = json.loads(result.stdout)
        self.assertIn("normal", value["modes"])
        self.assertIn("malformed-frame", value["modes"])
        self.assertIn("duplicate-receipt", value["modes"])
        self.assertEqual(value["framing"], "u32be-length-prefixed-protobuf")
        self.assertEqual(value["duplicate_unit"], "complete-worker-frame")
        self.assertFalse(value["production_protocol_decisions"])

    def test_owner_lifecycle_case_receives_frozen_in_process_fixture_inputs(self) -> None:
        value = _benchmark_fixture_inputs(
            "protocol-and-owner-lifecycle",
            "benchmark_fixture",
            "owner-lifecycle-device-executor-v1",
            {
                "daemon_outcome": "failure_during_turn",
                "client_protocol_relation": "incompatible",
            },
        )
        self.assertEqual(
            value["schema_version"], "turnvector.benchmark.owner-lifecycle-fixture.v1"
        )
        self.assertEqual(value["fixture_id"], "owner-lifecycle-device-executor-v1")
        self.assertEqual(value["execution_provenance"], "benchmark_fixture")
        self.assertTrue(value["same_process"])
        self.assertEqual(value["daemon_processes"], 1)
        self.assertFalse(value["real_backend_interface"])
        self.assertFalse(value["separate_mlx_worker"])
        self.assertEqual(value["daemon_outcome"], "failure_during_turn")
        self.assertEqual(value["client_protocol_relation"], "incompatible")
        self.assertEqual(value["injection"], "inject-failure_during_turn")
        self.assertEqual(value["max_frame_bytes"], 1024)
        self.assertFalse(value["claimable"])
        # The successor runtime produces no subprocess worker launch command.
        self.assertNotIn("command_prefix", value)
        self.assertNotIn("mode", value)
        # Non-owner lanes receive no benchmark fixture inputs.
        self.assertEqual(
            _benchmark_fixture_inputs(
                "core-event-replay",
                "benchmark_fixture",
                "owner-lifecycle-device-executor-v1",
                {"daemon_outcome": "normal"},
            ),
            {},
        )

    def test_owner_lifecycle_fixture_inputs_are_suppressed_under_production_subject(
        self,
    ) -> None:
        # production_subject provenance never emits a fixture payload, even on
        # the owner-lifecycle lane: fixture evidence cannot leak into
        # production-subject case steps.
        self.assertEqual(
            _benchmark_fixture_inputs(
                "protocol-and-owner-lifecycle",
                "production_subject",
                None,
                {
                    "daemon_outcome": "normal",
                    "client_protocol_relation": "exact",
                },
            ),
            {},
        )

    def test_owner_lifecycle_fixture_inputs_reject_a_mismatched_fixture_identity(
        self,
    ) -> None:
        # A benchmark_fixture LaneContext bound to a different fixture ID must
        # fail closed instead of emitting the owner-lifecycle payload.
        with self.assertRaisesRegex(ContractError, "requires fixture_id"):
            _benchmark_fixture_inputs(
                "protocol-and-owner-lifecycle",
                "benchmark_fixture",
                "some-other-fixture",
                {
                    "daemon_outcome": "normal",
                    "client_protocol_relation": "exact",
                },
            )

    def test_worker_proxy_uses_complete_u32be_frames(self) -> None:
        first = struct.pack(">I", 3) + b"one"
        second = struct.pack(">I", 3) + b"two"
        buffer = bytearray(first + second[:5])
        self.assertEqual(pop_u32be_frames(buffer, 1024), [first])
        buffer.extend(second[5:])
        self.assertEqual(pop_u32be_frames(buffer, 1024), [second])
        self.assertFalse(buffer)

    def test_worker_proxy_fault_frames_are_bounded_and_distinct(self) -> None:
        malformed = injected_frame("malformed-frame", 1024)
        incompatible = injected_frame("incompatible-handshake", 1024)
        self.assertEqual(struct.unpack(">I", malformed)[0], 1025)
        self.assertEqual(struct.unpack(">I", incompatible[:4])[0], len(incompatible) - 4)
        self.assertNotEqual(malformed, incompatible)

    def test_worker_proxy_preserves_data_through_downstream_backpressure(self) -> None:
        controller, daemon = socket.socketpair()
        worker = (
            "import os,socket,time;"
            "endpoint=socket.socket(fileno=int(os.environ['TURNVECTOR_BACKEND_FD']));"
            "time.sleep(0.25);"
            "chunks=[];"
            "chunk=endpoint.recv(65536);"
            "\nwhile chunk:\n chunks.append(chunk); chunk=endpoint.recv(65536)\n"
            "endpoint.sendall(b''.join(chunks));endpoint.shutdown(socket.SHUT_WR)"
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(WORKER_PROXY),
                "--mode",
                "normal",
                "--daemon-fd",
                str(daemon.fileno()),
                "--",
                sys.executable,
                "-c",
                worker,
            ],
            pass_fds=(daemon.fileno(),),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        daemon.close()
        controller.settimeout(5)
        payload = b"x" * (2 * 1024 * 1024)
        received = bytearray()
        transport_error = None
        try:
            controller.sendall(payload)
            controller.shutdown(socket.SHUT_WR)
            while True:
                chunk = controller.recv(64 * 1024)
                if not chunk:
                    break
                received.extend(chunk)
        except OSError as error:
            transport_error = error
        finally:
            controller.close()
        try:
            _, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate(timeout=5)
            self.fail(f"worker proxy did not terminate; stderr={stderr!r}")

        self.assertIsNone(transport_error, stderr)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(bytes(received), payload)

    def test_persistence_corruption_mutates_a_real_hash_bound_file(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = self.staged_file(root, "snapshot.bin", "snapshot_payload", b"abcdefgh")
        result = apply_persistence_fault(
            root,
            "corruption",
            self.persistence_stage(process_id=os.getpid(), fault_target=target),
            self.allowed_python(os.getpid()),
        )
        self.assertEqual(result["action"], "flip-byte")
        self.assertNotEqual(
            result["target_before"]["sha256"], result["target_after"]["sha256"]
        )

    def test_persistence_concurrent_publication_uses_atomic_replace(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = self.staged_file(
            root, "published.bin", "snapshot_publication", b"before"
        )
        replacement = self.staged_file(
            root, "replacement.bin", "snapshot_publication", b"after"
        )
        result = apply_persistence_fault(
            root,
            "concurrent_reader_writer",
            self.persistence_stage(
                process_id=os.getpid(),
                fault_target=target,
                replacement=replacement,
            ),
            self.allowed_python(os.getpid()),
        )
        self.assertEqual(result["action"], "atomic-replace")
        self.assertTrue(result["concurrent_read_sha256"])
        self.assertEqual((root / "published.bin").read_bytes(), b"after")

    def test_persistence_rejects_runtime_root_escape(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        stage = self.persistence_stage(
            process_id=os.getpid(),
            fault_target={
                "path": "../outside.bin",
                "size": 1,
                "sha256": "0" * 64,
                "role": "snapshot_payload",
            },
        )
        with self.assertRaisesRegex(ContractError, "escapes"):
            apply_persistence_fault(
                root, "corruption", stage, self.allowed_python(os.getpid())
            )

    def test_persistence_rejects_replacement_before_payload_validation(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = self.staged_file(root, "snapshot.bin", "snapshot_payload", b"payload")
        replacement = self.staged_file(
            root, "unexpected.bin", "snapshot_payload", b""
        )

        with self.assertRaisesRegex(ContractError, "must not stage a replacement"):
            apply_persistence_fault(
                root,
                "corruption",
                self.persistence_stage(
                    process_id=os.getpid(),
                    fault_target=target,
                    replacement=replacement,
                ),
                self.allowed_python(os.getpid()),
            )

    def test_persistence_process_exit_race_is_a_contract_failure(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        marker = self.staged_file(
            root, "payload-ready", "snapshot_payload_staged", b"ready"
        )
        stage = self.persistence_stage(
            process_id=os.getpid(),
            phase_marker=marker,
        )

        real_kill = os.kill

        def exit_before_sigterm(process_id: int, selected_signal: int) -> None:
            if selected_signal == signal.SIGTERM:
                raise ProcessLookupError
            real_kill(process_id, selected_signal)

        with patch(
            "turnvector_benchmark.lane_runner.os.kill",
            side_effect=exit_before_sigterm,
        ):
            with self.assertRaisesRegex(ContractError, "exited before SIGTERM"):
                apply_persistence_fault(
                    root,
                    "interrupted_payload",
                    stage,
                    self.allowed_python(os.getpid()),
                )

    def test_persistence_sigterm_is_limited_to_a_hash_bound_process(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        marker = self.staged_file(
            root, "payload-ready", "snapshot_payload_staged", b"ready"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.1)
        def stop_process() -> None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

        self.addCleanup(stop_process)
        stage = self.persistence_stage(
            process_id=process.pid,
            phase_marker=marker,
        )
        with self.assertRaisesRegex(ContractError, "hash-bound"):
            apply_persistence_fault(root, "interrupted_payload", stage, {})
        self.assertIsNone(process.poll())

        result = apply_persistence_fault(
            root,
            "interrupted_payload",
            stage,
            self.allowed_python(process.pid),
        )
        process.wait(timeout=5)
        self.assertEqual(result["action"], "sigterm")
        self.assertTrue(result["process_terminated"])


if __name__ == "__main__":
    unittest.main()
