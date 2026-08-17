from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import stat
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .core import ContractError


PROTOCOL_FAMILY = "turnvector.data-plane"
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 1
MAX_BINARY_FRAME_BYTES = 64 * 1024 * 1024
MAX_QUALIFICATION_SERVER_WRITE_TIMEOUT_MS = 5_000
CLIENT_BUILD_IDENTITY = "turnvector-benchmark-data-plane-client-v1"
SEED = 20260812


class DataPlaneEnvironmentError(RuntimeError):
    pass


class DataPlaneDisconnected(ContractError):
    pass


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _exact_fields(value: Mapping[str, Any], fields: Sequence[str], where: str) -> None:
    expected = set(fields)
    if set(value) != expected:
        raise ContractError(
            f"{where} fields differ: missing={sorted(expected - set(value))!r}, "
            f"unknown={sorted(set(value) - expected)!r}"
        )


def _positive_int(value: Any, where: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ContractError(f"{where} must be an integer in [1, {maximum}]")
    return value


def _positive_number(value: Any, where: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 < parsed <= maximum:
        raise ContractError(f"{where} must be finite and in (0, {maximum}]")
    return parsed


def _sha256_text(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(f"{where} must be a lowercase SHA256")
    return value


def _protocol_root() -> Path:
    return Path(__file__).resolve().parent.parent / "protocols"


def protocol_lock() -> Mapping[str, Any]:
    root = _protocol_root()
    lock_path = root / "data-plane-v1.lock.json"
    try:
        value = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read Data Plane protocol lock: {error}") from error
    if not isinstance(value, dict):
        raise ContractError("Data Plane protocol lock must be an object")
    _exact_fields(
        value,
        [
            "schema_version",
            "family",
            "major",
            "minor",
            "source",
            "source_sha256",
            "descriptor_set",
            "descriptor_sha256",
            "generator",
        ],
        "Data Plane protocol lock",
    )
    if (
        value["schema_version"] != "turnvector.benchmark.protocol-lock.v1"
        or value["family"] != PROTOCOL_FAMILY
        or value["major"] != PROTOCOL_MAJOR
        or value["minor"] != PROTOCOL_MINOR
    ):
        raise ContractError("Data Plane protocol lock identity is invalid")
    source = root / str(value["source"])
    descriptor = root / str(value["descriptor_set"])
    for path, field in (
        (source, "source_sha256"),
        (descriptor, "descriptor_sha256"),
    ):
        if not path.is_file():
            raise ContractError(f"Data Plane protocol file is missing: {path}")
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != _sha256_text(value[field], f"protocol lock {field}"):
            raise ContractError(f"Data Plane protocol {path.name} differs from its lock")
    return value


@dataclass(frozen=True)
class DataPlaneDescriptor:
    socket_path: Path
    descriptor_sha256: str
    max_frame_bytes: int
    max_outstanding_commands: int
    max_command_bytes: int
    connect_timeout_seconds: float
    frame_timeout_seconds: float
    max_server_write_timeout_ms: int
    model_revisions: Mapping[str, str]
    process_ids: Tuple[int, ...]

    @classmethod
    def parse(cls, value: Any) -> "DataPlaneDescriptor":
        descriptor = _object(value, "hello_ack.data_plane")
        _exact_fields(
            descriptor,
            [
                "protocol_family",
                "protocol_major",
                "protocol_minor",
                "descriptor_sha256",
                "transport",
                "socket_path",
                "limits",
                "timeouts",
                "model_revisions",
                "process_ids",
            ],
            "hello_ack.data_plane",
        )
        lock = protocol_lock()
        if (
            descriptor["protocol_family"] != PROTOCOL_FAMILY
            or descriptor["protocol_major"] != PROTOCOL_MAJOR
            or descriptor["protocol_minor"] != PROTOCOL_MINOR
            or descriptor["transport"] != "unix_stream"
        ):
            raise ContractError("Data Plane endpoint does not implement the qualification protocol")
        digest = _sha256_text(
            descriptor["descriptor_sha256"], "hello_ack.data_plane.descriptor_sha256"
        )
        if digest != lock["descriptor_sha256"]:
            raise ContractError("Data Plane endpoint descriptor hash differs from the qualification lock")

        socket_text = descriptor["socket_path"]
        if not isinstance(socket_text, str) or not socket_text:
            raise ContractError("hello_ack.data_plane.socket_path must be a non-empty string")
        socket_path = Path(socket_text)
        if not socket_path.is_absolute():
            raise ContractError("hello_ack.data_plane.socket_path must be absolute")
        try:
            mode = os.lstat(socket_path).st_mode
        except OSError as error:
            raise DataPlaneEnvironmentError(
                f"Data Plane socket is unavailable: {socket_path}: {error}"
            ) from error
        if stat.S_ISLNK(mode) or not stat.S_ISSOCK(mode):
            raise ContractError("hello_ack.data_plane.socket_path must name a Unix socket directly")

        limits = _object(descriptor["limits"], "hello_ack.data_plane.limits")
        _exact_fields(
            limits,
            ["max_frame_bytes", "max_outstanding_commands", "max_command_bytes"],
            "hello_ack.data_plane.limits",
        )
        max_frame = _positive_int(
            limits["max_frame_bytes"],
            "hello_ack.data_plane.limits.max_frame_bytes",
            MAX_BINARY_FRAME_BYTES,
        )
        max_commands = _positive_int(
            limits["max_outstanding_commands"],
            "hello_ack.data_plane.limits.max_outstanding_commands",
            65_536,
        )
        max_command = _positive_int(
            limits["max_command_bytes"],
            "hello_ack.data_plane.limits.max_command_bytes",
            max_frame,
        )

        timeouts = _object(descriptor["timeouts"], "hello_ack.data_plane.timeouts")
        _exact_fields(
            timeouts,
            ["connect_seconds", "frame_seconds", "max_server_write_timeout_ms"],
            "hello_ack.data_plane.timeouts",
        )
        max_server_timeout = _positive_int(
            timeouts["max_server_write_timeout_ms"],
            "hello_ack.data_plane.timeouts.max_server_write_timeout_ms",
            MAX_QUALIFICATION_SERVER_WRITE_TIMEOUT_MS,
        )

        revisions = _object(
            descriptor["model_revisions"], "hello_ack.data_plane.model_revisions"
        )
        _exact_fields(revisions, ["dense", "moe"], "hello_ack.data_plane.model_revisions")
        parsed_revisions = {
            architecture: _sha256_text(
                revisions[architecture],
                f"hello_ack.data_plane.model_revisions.{architecture}",
            )
            for architecture in ("dense", "moe")
        }
        raw_pids = descriptor["process_ids"]
        if not isinstance(raw_pids, list) or not raw_pids:
            raise ContractError("hello_ack.data_plane.process_ids must be a non-empty array")
        process_ids = tuple(
            _positive_int(pid, f"hello_ack.data_plane.process_ids[{index}]", 2**31 - 1)
            for index, pid in enumerate(raw_pids)
        )
        if len(process_ids) != len(set(process_ids)):
            raise ContractError("hello_ack.data_plane.process_ids must not contain duplicates")
        return cls(
            socket_path=socket_path,
            descriptor_sha256=digest,
            max_frame_bytes=max_frame,
            max_outstanding_commands=max_commands,
            max_command_bytes=max_command,
            connect_timeout_seconds=_positive_number(
                timeouts["connect_seconds"],
                "hello_ack.data_plane.timeouts.connect_seconds",
                60.0,
            ),
            frame_timeout_seconds=_positive_number(
                timeouts["frame_seconds"],
                "hello_ack.data_plane.timeouts.frame_seconds",
                300.0,
            ),
            max_server_write_timeout_ms=max_server_timeout,
            model_revisions=parsed_revisions,
            process_ids=process_ids,
        )


@dataclass(frozen=True)
class _ProtocolTypes:
    ClientFrame: Any
    ServerFrame: Any
    service_classes: Mapping[str, int]
    request_states: Mapping[int, str]
    turn_phases: Mapping[int, str]
    cancellation_states: Mapping[int, str]


_PROTOCOL_TYPES: Optional[_ProtocolTypes] = None


def _load_protocol_types() -> _ProtocolTypes:
    global _PROTOCOL_TYPES
    if _PROTOCOL_TYPES is not None:
        return _PROTOCOL_TYPES
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    except ImportError as error:
        raise DataPlaneEnvironmentError(
            "Data Plane qualification requires protobuf==6.33.6"
        ) from error
    lock = protocol_lock()
    descriptor_path = _protocol_root() / str(lock["descriptor_set"])
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    try:
        descriptor_set.ParseFromString(descriptor_path.read_bytes())
    except Exception as error:
        raise ContractError(f"cannot parse locked Data Plane descriptor set: {error}") from error
    pool = descriptor_pool.DescriptorPool()
    for file_descriptor in descriptor_set.file:
        pool.Add(file_descriptor)
    prefix = "turnvector.benchmark.data_plane.v1"

    def message(name: str) -> Any:
        return message_factory.GetMessageClass(
            pool.FindMessageTypeByName(f"{prefix}.{name}")
        )

    def enum(name: str) -> Mapping[int, str]:
        descriptor = pool.FindEnumTypeByName(f"{prefix}.{name}")
        return {
            value.number: value.name.lower()
            for value in descriptor.values
            if value.number != 0
        }

    service_descriptor = pool.FindEnumTypeByName(f"{prefix}.ServiceClass")
    service_classes = {
        "interactive": service_descriptor.values_by_name[
            "SERVICE_CLASS_INTERACTIVE"
        ].number,
        "standard": service_descriptor.values_by_name["SERVICE_CLASS_STANDARD"].number,
        "background": service_descriptor.values_by_name[
            "SERVICE_CLASS_BACKGROUND"
        ].number,
    }
    _PROTOCOL_TYPES = _ProtocolTypes(
        ClientFrame=message("ClientFrame"),
        ServerFrame=message("ServerFrame"),
        service_classes=service_classes,
        request_states={
            number: name.removeprefix("request_state_")
            for number, name in enum("RequestState").items()
        },
        turn_phases={
            number: name.removeprefix("turn_phase_")
            for number, name in enum("TurnPhase").items()
        },
        cancellation_states={
            number: name.removeprefix("cancellation_state_")
            for number, name in enum("CancellationState").items()
        },
    )
    return _PROTOCOL_TYPES


def _enum(mapping: Mapping[int, str], value: int, where: str) -> str:
    try:
        return mapping[value]
    except KeyError as error:
        raise ContractError(f"{where} contains an unknown or unspecified enum value {value}") from error


class DataPlaneClient:
    """Benchmark-owned client for one bounded production Data Plane connection."""

    def __init__(self, descriptor: DataPlaneDescriptor) -> None:
        self.descriptor = descriptor
        self.protocol = _load_protocol_types()
        self.socket: Optional[socket.socket] = None
        self.command_id = 0
        self.max_frame_bytes = descriptor.max_frame_bytes
        self.server_write_timeouts_ms: Tuple[int, int] = (0, 0)
        self.trace: List[Mapping[str, Any]] = []

    def __enter__(self) -> "DataPlaneClient":
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.descriptor.connect_timeout_seconds)
        try:
            connection.connect(str(self.descriptor.socket_path))
        except OSError as error:
            connection.close()
            raise DataPlaneEnvironmentError(
                f"cannot connect to Data Plane socket {self.descriptor.socket_path}: {error}"
            ) from error
        connection.settimeout(self.descriptor.frame_timeout_seconds)
        self.socket = connection
        self.negotiate()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
            self.socket = None

    def _send(self, message: Any, kind: str) -> None:
        if self.socket is None:
            raise DataPlaneDisconnected("Data Plane connection is closed")
        payload = message.SerializeToString(deterministic=True)
        if not payload or len(payload) > min(self.max_frame_bytes, self.descriptor.max_command_bytes):
            raise ContractError(f"outgoing Data Plane {kind} frame is empty or over its bound")
        try:
            self.socket.sendall(struct.pack(">I", len(payload)) + payload)
        except OSError as error:
            raise DataPlaneDisconnected(f"Data Plane write failed: {error}") from error
        self.trace.append(
            {
                "direction": "sent",
                "kind": kind,
                "monotonic_ns": time.monotonic_ns(),
                "payload_bytes": len(payload),
            }
        )

    def _read_exact(self, size: int) -> bytes:
        if self.socket is None:
            raise DataPlaneDisconnected("Data Plane connection is closed")
        chunks: List[bytes] = []
        remaining = size
        while remaining:
            try:
                chunk = self.socket.recv(remaining)
            except socket.timeout as error:
                raise DataPlaneEnvironmentError("Data Plane frame timed out") from error
            except OSError as error:
                raise DataPlaneDisconnected(f"Data Plane read failed: {error}") from error
            if not chunk:
                raise DataPlaneDisconnected("Data Plane connection reached EOF")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _receive(self) -> Mapping[str, Any]:
        header = self._read_exact(4)
        declared = struct.unpack(">I", header)[0]
        if declared == 0 or declared > self.max_frame_bytes:
            raise ContractError(
                f"Data Plane declared frame length {declared} outside [1, {self.max_frame_bytes}]"
            )
        payload = self._read_exact(declared)
        frame = self.protocol.ServerFrame()
        try:
            consumed = frame.ParseFromString(payload)
        except Exception as error:
            raise ContractError(f"Data Plane emitted malformed Protobuf: {error}") from error
        if consumed != len(payload):
            raise ContractError("Data Plane Protobuf parser did not consume the complete frame")
        kind = frame.WhichOneof("frame")
        if kind is None:
            raise ContractError("Data Plane ServerFrame contains no frame variant")
        received_ns = time.monotonic_ns()
        normalized = self._normalize_server_frame(frame, kind, received_ns, declared)
        self.trace.append(
            {
                "direction": "received",
                "kind": kind,
                "monotonic_ns": received_ns,
                "payload_bytes": declared,
            }
        )
        return normalized

    def _normalize_server_frame(
        self, frame: Any, kind: str, received_ns: int, payload_bytes: int
    ) -> Mapping[str, Any]:
        common = {"kind": kind, "monotonic_ns": received_ns, "payload_bytes": payload_bytes}
        if kind == "hello_ack":
            ack = frame.hello_ack
            return {
                **common,
                "family": ack.family,
                "major": ack.major,
                "selected_minor": ack.selected_minor,
                "selected_descriptor_sha256": bytes(
                    ack.selected_descriptor_sha256
                ).hex(),
                "effective_capability_ids": list(ack.effective_capability_ids),
                "effective_limits": {
                    "max_frame_bytes": ack.effective_limits.offered_max_frame_bytes,
                    "max_outstanding_commands": ack.effective_limits.offered_max_outstanding_commands,
                    "max_command_bytes": ack.effective_limits.offered_max_command_bytes,
                },
                "daemon_instance_id": bytes(ack.daemon_instance_id).hex(),
                "installation_policy_identity": bytes(
                    ack.installation_policy_identity
                ).hex(),
                "no_progress_write_timeout_ms": ack.no_progress_write_timeout_ms,
                "maximum_completion_write_timeout_ms": ack.maximum_completion_write_timeout_ms,
            }
        if kind == "protocol_error":
            error = frame.protocol_error
            raise ContractError(
                "Data Plane protocol error: "
                f"error_code={error.error_code}, reason_code={error.reason_code}"
            )
        if kind == "direct_response":
            response = frame.direct_response
            variant = response.WhichOneof("response")
            if variant is None:
                raise ContractError("Data Plane DirectResponse contains no response variant")
            result: Dict[str, Any] = {
                **common,
                "command_id": response.command_id,
                "command_ingress_closed": response.command_ingress_closed,
                "response": variant,
            }
            if variant == "request_accepted":
                accepted = response.request_accepted
                result.update(
                    {
                        "request_id": bytes(accepted.request_id).hex(),
                        "frozen_model_revision": bytes(
                            accepted.frozen_model_revision
                        ).hex(),
                        "status_version": accepted.status_version,
                        "state": _enum(
                            self.protocol.request_states,
                            accepted.state,
                            "RequestAccepted.state",
                        ),
                        "reservation_created": accepted.reservation_created,
                        "backend_handle_created": accepted.backend_handle_created,
                    }
                )
            elif variant == "cancellation_accepted":
                cancellation = response.cancellation_accepted
                result.update(
                    {
                        "request_id": bytes(cancellation.request_id).hex(),
                        "event_sequence": cancellation.event_sequence,
                    }
                )
            else:
                error = response.error
                result.update(
                    {
                        "error_code": error.error_code,
                        "reason_code": error.reason_code,
                        "retryable": error.retryable,
                        "request_id": (
                            bytes(error.request_id).hex()
                            if error.HasField("request_id")
                            else None
                        ),
                    }
                )
            return result
        if kind == "status_update":
            status = frame.status_update
            return {
                **common,
                "request_id": bytes(status.request_id).hex(),
                "status_version": status.status_version,
                "state": _enum(
                    self.protocol.request_states, status.state, "RequestStatusUpdate.state"
                ),
                "phase": _enum(
                    self.protocol.turn_phases, status.phase, "RequestStatusUpdate.phase"
                ),
                "input_token_progress": status.input_token_progress,
                "generated_token_progress": status.generated_token_progress,
                "last_output_sequence": (
                    status.last_output_sequence
                    if status.HasField("last_output_sequence")
                    else None
                ),
                "cancellation_state": _enum(
                    self.protocol.cancellation_states,
                    status.cancellation_state,
                    "RequestStatusUpdate.cancellation_state",
                ),
                "terminal": status.terminal,
                "terminal_reason_code": (
                    status.terminal_reason_code
                    if status.HasField("terminal_reason_code")
                    else None
                ),
                "reservation_created": status.reservation_created,
                "backend_handle_created": status.backend_handle_created,
                "history_gap": status.history_gap,
            }
        if kind == "output_frame":
            output = frame.output_frame
            return {
                **common,
                "request_id": bytes(output.request_id).hex(),
                "output_sequence": output.output_sequence,
                "token_ids": list(output.token_ids),
                "status_version": output.status_version,
                "terminal": output.terminal,
                "terminal_reason_code": (
                    output.terminal_reason_code
                    if output.HasField("terminal_reason_code")
                    else None
                ),
                "output_capacity_reserved": output.output_capacity_reserved,
                "publication_id": bytes(output.publication_id).hex(),
            }
        raise ContractError(f"unknown Data Plane frame variant {kind!r}")

    def negotiate(self) -> Mapping[str, Any]:
        frame = self.protocol.ClientFrame()
        hello = frame.hello
        hello.family = PROTOCOL_FAMILY
        hello.major = PROTOCOL_MAJOR
        minor = hello.minors.add()
        minor.minor = PROTOCOL_MINOR
        minor.descriptor_sha256 = bytes.fromhex(self.descriptor.descriptor_sha256)
        hello.limits.offered_max_frame_bytes = self.descriptor.max_frame_bytes
        hello.limits.offered_max_outstanding_commands = (
            self.descriptor.max_outstanding_commands
        )
        hello.limits.offered_max_command_bytes = self.descriptor.max_command_bytes
        hello.build_identity = CLIENT_BUILD_IDENTITY
        hello.protocol_support_manifest_sha256 = bytes.fromhex(
            self.descriptor.descriptor_sha256
        )
        self._send(frame, "hello")
        response = self._receive()
        if response["kind"] != "hello_ack":
            raise ContractError("Data Plane first response must be HelloAck")
        if (
            response["family"] != PROTOCOL_FAMILY
            or response["major"] != PROTOCOL_MAJOR
            or response["selected_minor"] != PROTOCOL_MINOR
            or response["selected_descriptor_sha256"]
            != self.descriptor.descriptor_sha256
        ):
            raise ContractError("Data Plane did not select the exact locked protocol entry")
        capabilities = response["effective_capability_ids"]
        if capabilities != sorted(set(capabilities)) or any(value == 0 for value in capabilities):
            raise ContractError("Data Plane effective capability IDs are not sorted unique nonzero")
        limits = response["effective_limits"]
        for key, offered in (
            ("max_frame_bytes", self.descriptor.max_frame_bytes),
            ("max_outstanding_commands", self.descriptor.max_outstanding_commands),
            ("max_command_bytes", self.descriptor.max_command_bytes),
        ):
            if not isinstance(limits[key], int) or not 0 < limits[key] <= offered:
                raise ContractError(f"Data Plane effective {key} exceeds the client offer")
        if len(response["daemon_instance_id"]) != 32:
            raise ContractError("Data Plane daemon instance ID must encode exactly 16 bytes")
        if len(response["installation_policy_identity"]) != 64:
            raise ContractError("Data Plane installation policy identity must encode 32 bytes")
        write_timeouts = (
            response["no_progress_write_timeout_ms"],
            response["maximum_completion_write_timeout_ms"],
        )
        if any(
            not isinstance(value, int)
            or not 0 < value <= self.descriptor.max_server_write_timeout_ms
            for value in write_timeouts
        ):
            raise ContractError("Data Plane write durations exceed the qualification hard maximum")
        self.max_frame_bytes = limits["max_frame_bytes"]
        self.server_write_timeouts_ms = write_timeouts
        return response

    def _next_command(self) -> int:
        self.command_id += 1
        if self.command_id >= 2**64:
            raise ContractError("Data Plane command ID overflow")
        return self.command_id

    def submit(
        self,
        *,
        model_revision: str,
        input_token_ids: Sequence[int],
        max_output_tokens: int,
        sampling_seed: int,
        service_class: str,
    ) -> Mapping[str, Any]:
        if service_class not in self.protocol.service_classes:
            raise ContractError(f"unknown Service Class {service_class!r}")
        if not input_token_ids or any(
            isinstance(token, bool) or not isinstance(token, int) or not 0 <= token < 2**32
            for token in input_token_ids
        ):
            raise ContractError("Data Plane input tokens must be a non-empty uint32 sequence")
        frame = self.protocol.ClientFrame()
        command = frame.command
        command.command_id = self._next_command()
        submit = command.submit_request
        submit.model_revision = bytes.fromhex(_sha256_text(model_revision, "model revision"))
        submit.input_token_ids.extend(input_token_ids)
        submit.max_output_tokens = _positive_int(
            max_output_tokens, "max output tokens", 2**32 - 1
        )
        submit.sampling_seed = _positive_int(
            sampling_seed, "sampling seed", 2**64 - 1
        )
        submit.service_class = self.protocol.service_classes[service_class]
        self._send(frame, "submit_request")
        response = self._receive()
        if response["kind"] != "direct_response" or response["command_id"] != command.command_id:
            raise ContractError("causal Data Plane event preceded the Submit Direct Response")
        if response["response"] == "error":
            raise ContractError(
                "Data Plane rejected qualification request: "
                f"error_code={response['error_code']}, reason_code={response['reason_code']}"
            )
        if response["response"] != "request_accepted":
            raise ContractError("Submit Direct Response is not RequestAccepted")
        if (
            len(response["request_id"]) != 32
            or response["frozen_model_revision"] != model_revision
            or response["status_version"] != 1
            or response["state"] != "accepted"
            or response["reservation_created"]
            or response["backend_handle_created"]
        ):
            raise ContractError("RequestAccepted violates the pre-Admission contract")
        return response

    def cancel(self, request_id: str) -> Tuple[Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
        if len(request_id) != 32:
            raise ContractError("request ID must encode exactly 16 bytes")
        frame = self.protocol.ClientFrame()
        command = frame.command
        command.command_id = self._next_command()
        command.cancel_request.request_id = bytes.fromhex(request_id)
        self._send(frame, "cancel_request")
        intervening: List[Mapping[str, Any]] = []
        for _ in range(10_000):
            response = self._receive()
            if response["kind"] != "direct_response":
                intervening.append(response)
                continue
            if response["command_id"] != command.command_id:
                raise ContractError("Data Plane returned an unexpected Direct Response command ID")
            if response["response"] != "cancellation_accepted":
                raise ContractError("Cancel Direct Response is not CancellationAccepted")
            if response["request_id"] != request_id or response["event_sequence"] == 0:
                raise ContractError("CancellationAccepted identity or Event Sequence is invalid")
            return response, tuple(intervening)
        raise ContractError("Data Plane omitted the Cancellation Direct Response")

    def receive(self) -> Mapping[str, Any]:
        return self._receive()

    def expect_backpressure_disconnect(self) -> None:
        if self.socket is None:
            raise DataPlaneDisconnected("Data Plane connection is closed")
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        stall_seconds = max(self.server_write_timeouts_ms) / 1000.0 + 0.1
        time.sleep(stall_seconds)
        self.socket.settimeout(0.5)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                chunk = self.socket.recv(65_536)
            except socket.timeout:
                break
            except OSError:
                return
            if not chunk:
                return
        raise ContractError("Data Plane did not disconnect the stalled backpressure client")


def _input_tokens(count: int, seed: int) -> List[int]:
    return [((seed + index * 17) % 32_000) + 1 for index in range(count)]


def _append_event(
    event: Mapping[str, Any],
    *,
    request_id: str,
    states: List[str],
    outputs: List[Mapping[str, Any]],
    status_versions: List[int],
) -> bool:
    kind = event["kind"]
    if kind == "direct_response":
        return False
    if event.get("request_id") != request_id:
        raise ContractError("Data Plane event belongs to an unexpected request")
    if kind == "status_update":
        if event["status_version"] <= 1 or (
            status_versions and event["status_version"] <= status_versions[-1]
        ):
            raise ContractError("Data Plane status versions are not strictly increasing")
        status_versions.append(event["status_version"])
        states.append(str(event["state"]))
        if event["state"] == "materialized" and not (
            event["reservation_created"] and event["backend_handle_created"]
        ):
            raise ContractError("Data Plane materialized state lacks reservation or Backend state")
        if event["terminal"] != (event["state"] == "terminal"):
            raise ContractError("Data Plane terminal flag and lifecycle state disagree")
        return bool(event["terminal"])
    if kind == "output_frame":
        if not event["token_ids"]:
            raise ContractError("Data Plane emitted an empty Output Frame")
        expected_sequence = (
            0
            if not outputs
            else int(outputs[-1]["output_sequence"])
            + len(outputs[-1]["token_ids"])
        )
        if event["output_sequence"] != expected_sequence:
            raise ContractError("Data Plane Output Sequence is not contiguous from zero")
        if len(event["publication_id"]) != 32:
            raise ContractError("Data Plane publication ID must encode exactly 16 bytes")
        outputs.append(dict(event))
        return False
    raise ContractError(f"unexpected Data Plane event {kind!r}")


def run_generation(
    descriptor: DataPlaneDescriptor,
    *,
    model_revision: str,
    service_class: str,
    input_token_count: int,
    max_output_tokens: int,
    seed: int,
    client_event: str = "none",
    start_barrier: Optional[threading.Barrier] = None,
) -> Mapping[str, Any]:
    states = ["accepted"]
    outputs: List[Mapping[str, Any]] = []
    status_versions: List[int] = []
    terminal = False
    cancellation: Optional[Mapping[str, Any]] = None
    disconnected = False
    started_ns = time.monotonic_ns()
    client = DataPlaneClient(descriptor)
    try:
        client.__enter__()
        if start_barrier is not None:
            start_barrier.wait(timeout=descriptor.frame_timeout_seconds)
        request_started_ns = time.monotonic_ns()
        accepted = client.submit(
            model_revision=model_revision,
            input_token_ids=_input_tokens(input_token_count, seed),
            max_output_tokens=max_output_tokens,
            sampling_seed=seed,
            service_class=service_class,
        )
        accepted_ns = int(accepted["monotonic_ns"])
        request_id = str(accepted["request_id"])
        if client_event == "disconnect":
            client.close()
            disconnected = True
        elif client_event == "backpressure_timeout":
            client.expect_backpressure_disconnect()
            disconnected = True
        else:
            if client_event == "cancel_before_receipt":
                cancellation, intervening = client.cancel(request_id)
                for event in intervening:
                    terminal |= _append_event(
                        event,
                        request_id=request_id,
                        states=states,
                        outputs=outputs,
                        status_versions=status_versions,
                    )
            cancel_after_output = client_event == "cancel_after_receipt"
            for _ in range(100_000):
                if terminal:
                    break
                event = client.receive()
                terminal |= _append_event(
                    event,
                    request_id=request_id,
                    states=states,
                    outputs=outputs,
                    status_versions=status_versions,
                )
                if cancel_after_output and outputs and cancellation is None:
                    cancellation, intervening = client.cancel(request_id)
                    for side_event in intervening:
                        terminal |= _append_event(
                            side_event,
                            request_id=request_id,
                            states=states,
                            outputs=outputs,
                            status_versions=status_versions,
                        )
            if not terminal:
                raise ContractError("Data Plane request did not reach a terminal Status Update")
        finished_ns = time.monotonic_ns()
        output_times = [int(item["monotonic_ns"]) for item in outputs]
        ttft_us = (
            (output_times[0] - accepted_ns) / 1000.0 if output_times else None
        )
        tpot_samples: List[float] = []
        for previous, current in zip(outputs, outputs[1:]):
            token_count = len(current["token_ids"])
            tpot_samples.append(
                (int(current["monotonic_ns"]) - int(previous["monotonic_ns"]))
                / 1000.0
                / token_count
            )
        return {
            "request_id": request_id,
            "client_event": client_event,
            "lifecycle": states,
            "outputs": [
                {
                    "publication_id": item["publication_id"],
                    "sequence": item["output_sequence"],
                    "reserved": item["output_capacity_reserved"],
                    "token_ids": list(item["token_ids"]),
                    "monotonic_ns": item["monotonic_ns"],
                }
                for item in outputs
            ],
            "accepted": dict(accepted),
            "cancellation": None if cancellation is None else dict(cancellation),
            "terminal_status_observed": terminal,
            "connection_disconnected": disconnected,
            "ttft_us": ttft_us,
            "tpot_samples_us": tpot_samples,
            "duration_us": (finished_ns - accepted_ns) / 1000.0,
            "generated_token_ids": [
                token for item in outputs for token in item["token_ids"]
            ],
            "protocol_trace": [dict(item) for item in client.trace],
            "request_started_ns": request_started_ns,
            "wall_started_ns": started_ns,
            "wall_finished_ns": finished_ns,
        }
    except BaseException:
        if start_barrier is not None:
            try:
                start_barrier.abort()
            except threading.BrokenBarrierError:
                pass
        raise
    finally:
        client.close()


def run_cross_model_case(
    descriptor: DataPlaneDescriptor, parameters: Mapping[str, Any]
) -> Mapping[str, Any]:
    pair = parameters["model_pair"]
    architectures = {
        "dense_dense": ("dense", "dense"),
        "dense_moe": ("dense", "moe"),
        "moe_moe": ("moe", "moe"),
    }.get(pair)
    if architectures is None:
        raise ContractError(f"unknown cross-model pair {pair!r}")
    contender = parameters["contender_work"]
    input_counts = (8192, 512) if contender == "long_prefill" else (512, 512)
    service_class = str(parameters["service_class"])
    seeds = (SEED, SEED + 1)

    baselines = [
        run_generation(
            descriptor,
            model_revision=descriptor.model_revisions[architecture],
            service_class=service_class,
            input_token_count=input_count,
            max_output_tokens=32,
            seed=seed,
        )
        for architecture, input_count, seed in zip(architectures, input_counts, seeds)
    ]
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="benchmark-data-plane") as executor:
        futures = [
            executor.submit(
                run_generation,
                descriptor,
                model_revision=descriptor.model_revisions[architecture],
                service_class=service_class,
                input_token_count=input_count,
                max_output_tokens=32,
                seed=seed,
                start_barrier=barrier,
            )
            for architecture, input_count, seed in zip(architectures, input_counts, seeds)
        ]
        concurrent_results: List[Optional[Mapping[str, Any]]] = [None] * len(futures)
        concurrent_errors: List[BaseException] = []
        for index, future in enumerate(futures):
            try:
                concurrent_results[index] = future.result()
            except BaseException as error:
                concurrent_errors.append(error)
        if concurrent_errors:
            root_error = next(
                (
                    error
                    for error in concurrent_errors
                    if not isinstance(error, threading.BrokenBarrierError)
                ),
                concurrent_errors[0],
            )
            raise root_error
        concurrent = [
            result for result in concurrent_results if result is not None
        ]
    ttft = [item["ttft_us"] for item in concurrent]
    if any(value is None or value <= 0 for value in ttft):
        raise ContractError("cross-model serving requires positive TTFT for both requests")
    tpot = [sample for item in concurrent for sample in item["tpot_samples_us"]]
    if not tpot or any(value <= 0 for value in tpot):
        raise ContractError("cross-model serving requires positive TPOT samples")
    tokens = sum(len(item["generated_token_ids"]) for item in concurrent)
    seconds = (
        max(int(item["wall_finished_ns"]) for item in concurrent)
        - min(int(item["request_started_ns"]) for item in concurrent)
    ) / 1_000_000_000.0
    if tokens <= 0 or seconds <= 0:
        raise ContractError("cross-model serving produced no measurable work")
    return {
        "baselines": baselines,
        "concurrent": concurrent,
        "progress_us": [item["duration_us"] for item in concurrent],
        "timing_us": [item["duration_us"] for item in concurrent],
        "latency_samples": [
            {"ttft_us": item["ttft_us"], "tpot_us": sample}
            for item in concurrent
            for sample in item["tpot_samples_us"]
        ],
        "throughput": {"tokens": tokens, "seconds": seconds},
        "outputs": [
            {
                "expected": baseline["generated_token_ids"],
                "observed": observed["generated_token_ids"],
            }
            for baseline, observed in zip(baselines, concurrent)
        ],
    }
