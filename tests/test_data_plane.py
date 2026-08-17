from __future__ import annotations

import os
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.data_plane import (
    DataPlaneDescriptor,
    _load_protocol_types,
    protocol_lock,
    run_cross_model_case,
    run_generation,
)
from turnvector_benchmark.expectation import load_expectation
from turnvector_benchmark.lane_contract import (
    CasePlan,
    expand_case_plan,
    load_all_lane_suites,
)
from turnvector_benchmark.lane_runner import (
    LaneContext,
    RequestServingLifecycleLaneRunner,
)
from turnvector_benchmark.subject import SubjectHello, SubjectIdentity


DENSE_REVISION = "11" * 32
MOE_REVISION = "22" * 32


class FixtureDataPlaneServer:
    def __init__(self, root: Path, *, oversized_hello: bool = False) -> None:
        self.path = root / "data-plane-v1.sock"
        self.oversized_hello = oversized_hello
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        self.listener.listen(1)
        self.protocol = _load_protocol_types()
        self.error: Optional[BaseException] = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.listener.close()
        self.thread.join(timeout=2.0)
        if self.thread.is_alive():
            raise RuntimeError("fixture Data Plane server did not stop")
        if self.error is not None:
            raise self.error

    @staticmethod
    def _read_exact(connection: socket.socket, size: int) -> bytes:
        value = b""
        while len(value) < size:
            chunk = connection.recv(size - len(value))
            if not chunk:
                raise EOFError("fixture client disconnected")
            value += chunk
        return value

    def _receive(self, connection: socket.socket) -> Any:
        size = struct.unpack(">I", self._read_exact(connection, 4))[0]
        frame = self.protocol.ClientFrame()
        frame.ParseFromString(self._read_exact(connection, size))
        return frame

    @staticmethod
    def _send(connection: socket.socket, frame: Any) -> None:
        payload = frame.SerializeToString(deterministic=True)
        connection.sendall(struct.pack(">I", len(payload)) + payload)

    def _run(self) -> None:
        try:
            connection, _ = self.listener.accept()
            with connection:
                hello = self._receive(connection)
                if hello.WhichOneof("frame") != "hello":
                    raise AssertionError("first client frame was not Hello")
                if self.oversized_hello:
                    connection.sendall(struct.pack(">I", 65 * 1024 * 1024))
                    return
                ack_frame = self.protocol.ServerFrame()
                ack = ack_frame.hello_ack
                ack.family = "turnvector.data-plane"
                ack.major = 1
                ack.selected_minor = 1
                ack.selected_descriptor_sha256 = bytes.fromhex(
                    protocol_lock()["descriptor_sha256"]
                )
                ack.effective_limits.offered_max_frame_bytes = 1_048_576
                ack.effective_limits.offered_max_outstanding_commands = 8
                ack.effective_limits.offered_max_command_bytes = 65_536
                ack.daemon_instance_id = bytes.fromhex("33" * 16)
                ack.installation_policy_identity = bytes.fromhex("44" * 32)
                ack.no_progress_write_timeout_ms = 50
                ack.maximum_completion_write_timeout_ms = 100
                self._send(connection, ack_frame)

                command_frame = self._receive(connection)
                command = command_frame.command
                if command.WhichOneof("command") != "submit_request":
                    raise AssertionError("fixture expected SubmitRequest")
                request_id = bytes.fromhex("55" * 16)
                direct_frame = self.protocol.ServerFrame()
                response = direct_frame.direct_response
                response.command_id = command.command_id
                accepted = response.request_accepted
                accepted.request_id = request_id
                accepted.frozen_model_revision = command.submit_request.model_revision
                accepted.status_version = 1
                accepted.state = 1
                accepted.reservation_created = False
                accepted.backend_handle_created = False
                self._send(connection, direct_frame)

                for version, state, reservation, backend in (
                    (2, 2, False, False),
                    (3, 4, True, False),
                    (4, 5, True, True),
                    (5, 6, True, True),
                    (6, 7, True, True),
                ):
                    status_frame = self.protocol.ServerFrame()
                    status = status_frame.status_update
                    status.request_id = request_id
                    status.status_version = version
                    status.state = state
                    status.phase = 2 if state < 7 else 3
                    status.cancellation_state = 1
                    status.reservation_created = reservation
                    status.backend_handle_created = backend
                    self._send(connection, status_frame)

                for sequence, token, publication_byte in ((0, 7, "66"), (1, 8, "77")):
                    output_frame = self.protocol.ServerFrame()
                    output = output_frame.output_frame
                    output.request_id = request_id
                    output.output_sequence = sequence
                    output.token_ids.append(token)
                    output.status_version = 6 + sequence
                    output.output_capacity_reserved = True
                    output.publication_id = bytes.fromhex(publication_byte * 16)
                    self._send(connection, output_frame)

                terminal_frame = self.protocol.ServerFrame()
                terminal = terminal_frame.status_update
                terminal.request_id = request_id
                terminal.status_version = 8
                terminal.state = 8
                terminal.phase = 1
                terminal.cancellation_state = 1
                terminal.terminal = True
                terminal.terminal_reason_code = 1
                terminal.reservation_created = False
                terminal.backend_handle_created = False
                terminal.last_output_sequence = 1
                self._send(connection, terminal_frame)
        except BaseException as error:
            self.error = error


class DataPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def descriptor(self, path: Path) -> DataPlaneDescriptor:
        return DataPlaneDescriptor.parse(
            {
                "protocol_family": "turnvector.data-plane",
                "protocol_major": 1,
                "protocol_minor": 1,
                "descriptor_sha256": protocol_lock()["descriptor_sha256"],
                "transport": "unix_stream",
                "socket_path": str(path),
                "limits": {
                    "max_frame_bytes": 1_048_576,
                    "max_outstanding_commands": 8,
                    "max_command_bytes": 65_536,
                },
                "timeouts": {
                    "connect_seconds": 1,
                    "frame_seconds": 1,
                    "max_server_write_timeout_ms": 1000,
                },
                "model_revisions": {
                    "dense": DENSE_REVISION,
                    "moe": MOE_REVISION,
                },
                "process_ids": [os.getpid()],
            }
        )

    @staticmethod
    def generation_result(seed: int, *, concurrent: bool) -> dict[str, Any]:
        starts = {20260812: 1_000_000_000, 20260813: 1_100_000_000}
        finishes = {20260812: 2_000_000_000, 20260813: 2_500_000_000}
        return {
            "ttft_us": 10.0,
            "tpot_samples_us": [2.0],
            "duration_us": 100.0,
            "generated_token_ids": [seed, seed + 1],
            "request_started_ns": starts[seed] if concurrent else starts[seed] - 1,
            "wall_started_ns": starts[seed] - 500_000_000,
            "wall_finished_ns": finishes[seed],
        }

    def test_cross_model_throughput_excludes_connection_setup(self) -> None:
        descriptor = SimpleNamespace(
            model_revisions={"dense": DENSE_REVISION, "moe": MOE_REVISION}
        )

        def generation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            return self.generation_result(
                kwargs["seed"], concurrent=kwargs.get("start_barrier") is not None
            )

        with patch("turnvector_benchmark.data_plane.run_generation", side_effect=generation):
            result = run_cross_model_case(
                descriptor,  # type: ignore[arg-type]
                {
                    "model_pair": "dense_moe",
                    "contender_work": "decode",
                    "service_class": "interactive",
                },
            )

        self.assertEqual(result["throughput"]["seconds"], 1.5)

    def test_cross_model_failure_reports_root_cause_before_broken_barrier(self) -> None:
        descriptor = SimpleNamespace(
            model_revisions={"dense": DENSE_REVISION, "moe": MOE_REVISION}
        )

        def generation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            if kwargs.get("start_barrier") is None:
                return self.generation_result(kwargs["seed"], concurrent=False)
            if kwargs["seed"] == 20260812:
                raise threading.BrokenBarrierError
            raise ContractError("root connection failure")

        with patch("turnvector_benchmark.data_plane.run_generation", side_effect=generation):
            with self.assertRaisesRegex(ContractError, "root connection failure"):
                run_cross_model_case(
                    descriptor,  # type: ignore[arg-type]
                    {
                        "model_pair": "dense_moe",
                        "contender_work": "decode",
                        "service_class": "interactive",
                    },
                )

    def test_locked_protocol_drives_a_real_unix_socket(self) -> None:
        server = FixtureDataPlaneServer(self.root)
        server.start()
        result = run_generation(
            self.descriptor(server.path),
            model_revision=DENSE_REVISION,
            service_class="interactive",
            input_token_count=4,
            max_output_tokens=2,
            seed=20260812,
        )
        server.stop()

        self.assertEqual(result["lifecycle"][0], "accepted")
        self.assertEqual(result["lifecycle"][-1], "terminal")
        self.assertEqual(result["generated_token_ids"], [7, 8])
        self.assertTrue(result["terminal_status_observed"])
        self.assertGreater(result["ttft_us"], 0)
        self.assertEqual(len(result["tpot_samples_us"]), 1)

    def test_declared_frame_length_is_rejected_before_payload_read(self) -> None:
        server = FixtureDataPlaneServer(self.root, oversized_hello=True)
        server.start()
        with self.assertRaisesRegex(ContractError, "declared frame length"):
            run_generation(
                self.descriptor(server.path),
                model_revision=DENSE_REVISION,
                service_class="standard",
                input_token_count=4,
                max_output_tokens=2,
                seed=20260812,
            )
        server.stop()

    def test_endpoint_descriptor_rejects_a_non_socket(self) -> None:
        regular = self.root / "not-a-socket"
        regular.write_text("x", encoding="ascii")
        with self.assertRaisesRegex(ContractError, "Unix socket"):
            self.descriptor(regular)

    def test_request_lane_uses_benchmark_client_and_writes_its_own_evidence(self) -> None:
        server = FixtureDataPlaneServer(self.root)
        server.start()
        descriptor_value = {
            "protocol_family": "turnvector.data-plane",
            "protocol_major": 1,
            "protocol_minor": 1,
            "descriptor_sha256": protocol_lock()["descriptor_sha256"],
            "transport": "unix_stream",
            "socket_path": str(server.path),
            "limits": {
                "max_frame_bytes": 1_048_576,
                "max_outstanding_commands": 8,
                "max_command_bytes": 65_536,
            },
            "timeouts": {
                "connect_seconds": 1,
                "frame_seconds": 1,
                "max_server_write_timeout_ms": 1000,
            },
            "model_revisions": {"dense": DENSE_REVISION, "moe": MOE_REVISION},
            "process_ids": [os.getpid()],
        }
        root = Path(__file__).resolve().parent.parent
        expectation = load_expectation(
            root / "expectations" / "turnvector-implementation-v1.json"
        )
        lane = expectation.lane("request-serving-lifecycle")
        suite = load_all_lane_suites(expectation)[lane.lane_id]
        full_plan = expand_case_plan(lane, suite)
        selected = next(
            case
            for case in full_plan.cases
            if case.parameters
            == {"service_class": "interactive", "client_event": "none"}
        )
        plan = CasePlan(lane_id=lane.lane_id, suite_id=suite.suite_id, cases=(selected,))
        artifact_root = self.root / "raw"
        artifact_root.mkdir()
        context = LaneContext(
            run_id="fixture-direct-run",
            lane=lane,
            suite=suite,
            plan=plan,
            artifact_root=artifact_root,
            frozen_thresholds={gate.metric: gate.expected for gate in lane.gates},
            external_inputs={},
        )
        hello = SubjectHello(
            identity=SubjectIdentity(
                name="implementation-fixture",
                version="1",
                kind="implementation",
                build_identity="implementation-fixture-build",
            ),
            supported_lanes={lane.lane_id: suite.protocol},
            binary_manifest=(),
            dependency_manifest=(),
            environment_identity={"fixture": True},
            data_plane=descriptor_value,
        )

        class Subject:
            def case_open(self, *args: Any) -> str:
                return "ready"

            def case_step(
                self,
                case_id: str,
                step_index: int,
                operation: str,
                payload: Any,
            ) -> Any:
                del case_id, operation
                if step_index == 1:
                    return {"prepared": True}
                return {
                    "production_trace": {
                        "request_id": payload["request_ids"][0],
                        "lifecycle": [
                            "accepted",
                            "preparing",
                            "admitted",
                            "materialized",
                            "queued",
                            "running",
                            "terminal",
                        ],
                        "outputs": [
                            {
                                "publication_id": "66" * 16,
                                "sequence": 0,
                                "reserved": True,
                            },
                            {
                                "publication_id": "77" * 16,
                                "sequence": 1,
                                "reserved": True,
                            },
                        ],
                        "cancellation_commit_sequence": None,
                        "receipt_commit_sequence": 10,
                        "disconnect_observed": False,
                        "backpressure_timeout_observed": False,
                        "terminal_status_emitted": True,
                    }
                }

            def case_close(self, case_id: str) -> Any:
                del case_id
                return {}, ()

        result = RequestServingLifecycleLaneRunner().run(
            context, Subject(), hello  # type: ignore[arg-type]
        )
        server.stop()

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.executed_case_count, 1)
        self.assertEqual(
            {artifact["id"] for artifact in result.artifacts},
            {"request_trace", "status_trace", "output_trace", "capacity_trace"},
        )


if __name__ == "__main__":
    unittest.main()
