from __future__ import annotations

import math
import os
import queue
import subprocess
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple

from ..core import ContractError
from .contracts import (
    LIFECYCLE_PROTOCOL_VERSION,
    MAX_ARRAY_ITEMS,
    MAX_JSONL_LINE_BYTES,
    bounded_array,
    bounded_string,
    boolean,
    canonical_json_bytes,
    decode_jsonl_line,
    encode_jsonl_line,
    environment_name,
    identifier,
    integer,
    sha256_digest,
    strict_object,
    unique_strings,
)


REQUEST_FIELDS = ("kind", "protocol_version", "request_id", "payload")
RESPONSE_FIELDS = (
    "kind",
    "protocol_version",
    "request_id",
    "status",
    "payload",
    "error",
)
REGISTERED_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "invalid_state",
        "unsupported",
        "prepare_failed",
        "start_failed",
        "endpoint_unavailable",
        "reset_failed",
        "stop_failed",
        "shutdown_failed",
        "internal_error",
    }
)


class LifecycleState(str, Enum):
    SPAWNED = "SPAWNED"
    NEGOTIATED = "NEGOTIATED"
    PREPARED = "PREPARED"
    STARTED = "STARTED"
    READY = "READY"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"


TRANSITIONS: Mapping[Tuple[LifecycleState, str], Tuple[str, LifecycleState]] = {
    (LifecycleState.SPAWNED, "hello"): ("hello_ack", LifecycleState.NEGOTIATED),
    (LifecycleState.NEGOTIATED, "prepare_session"): (
        "prepared",
        LifecycleState.PREPARED,
    ),
    (LifecycleState.PREPARED, "start_target"): (
        "target_started",
        LifecycleState.STARTED,
    ),
    (LifecycleState.STARTED, "describe_endpoint"): (
        "endpoint_ready",
        LifecycleState.READY,
    ),
    (LifecycleState.READY, "reset_state"): ("state_reset", LifecycleState.READY),
    (LifecycleState.READY, "stop_target"): (
        "target_stopped",
        LifecycleState.STOPPED,
    ),
    (LifecycleState.STARTED, "stop_target"): (
        "target_stopped",
        LifecycleState.STOPPED,
    ),
    (LifecycleState.STOPPED, "shutdown"): (
        "shutdown_ack",
        LifecycleState.TERMINATED,
    ),
}

REQUEST_PAYLOAD_FIELDS: Mapping[str, Tuple[str, ...]] = {
    "hello": ("requested_adapter_protocol",),
    "prepare_session": (
        "run_id",
        "session_id",
        "state_root",
        "target_sha256",
        "config_sha256",
        "model_sha256",
        "reset_policy",
    ),
    "start_target": (
        "argv",
        "config",
        "environment_names",
        "readiness_deadline_ms",
    ),
    "describe_endpoint": ("target_id", "session_id"),
    "reset_state": (
        "reset_ordinal",
        "reset_policy",
        "expected_prior_inventory_sha256",
    ),
    "stop_target": (
        "reason",
        "deadline_ms",
        "expected_process_group_leader_pid",
    ),
    "shutdown": ("session_id",),
}

RESPONSE_PAYLOAD_FIELDS: Mapping[str, Tuple[str, ...]] = {
    "hello_ack": (
        "adapter_protocol",
        "adapter_id",
        "adapter_version",
        "target_family",
        "lifecycle_capabilities",
    ),
    "prepared": (
        "run_id",
        "session_id",
        "target_sha256",
        "config_sha256",
        "model_sha256",
        "reset_policy",
        "initial_state_inventory_sha256",
    ),
    "target_started": (
        "process_group_leader_pid",
        "child_processes",
        "started_at_ns",
    ),
    "endpoint_ready": ("target_id", "session_id", "endpoint", "listener_owner"),
    "state_reset": (
        "reset_ordinal",
        "reset_policy",
        "pre_processes",
        "post_processes",
        "prior_inventory_sha256",
        "current_inventory_sha256",
    ),
    "target_stopped": (
        "process_group_leader_pid",
        "exit_records",
        "surviving_process_ids",
        "surviving_listeners",
    ),
    "shutdown_ack": ("session_id", "diagnostic_count", "no_live_children"),
}


class LifecycleRemoteError(ContractError):
    """A valid lifecycle error response; no state transition occurred."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"lifecycle adapter returned {code}: {message}")
        self.code = code
        self.message = message


def _bounded_json(value: Any, where: str, depth: int = 0) -> None:
    if depth > 16:
        raise ContractError(f"{where} exceeds the JSON nesting bound")
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str):
            bounded_string(value, where, maximum_bytes=16 * 1024, allow_empty=True)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{where} must not contain non-finite numbers")
        return
    if isinstance(value, list):
        bounded_array(value, where, maximum_items=MAX_ARRAY_ITEMS)
        for index, child in enumerate(value):
            _bounded_json(child, f"{where}[{index}]", depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ContractError(f"{where} exceeds the {MAX_ARRAY_ITEMS}-field bound")
        for key, child in value.items():
            bounded_string(key, f"{where} key", maximum_bytes=128)
            _bounded_json(child, f"{where}.{key}", depth + 1)
        return
    raise ContractError(f"{where} contains a non-JSON value")


def _argv(value: Any, where: str) -> Tuple[str, ...]:
    raw = bounded_array(value, where, maximum_items=256, allow_empty=False)
    return tuple(
        bounded_string(item, f"{where}[{index}]", maximum_bytes=4096)
        for index, item in enumerate(raw)
    )


def _process_records(value: Any, where: str) -> Tuple[Mapping[str, Any], ...]:
    records = []
    for index, raw in enumerate(
        bounded_array(value, where, maximum_items=1024, allow_empty=True)
    ):
        child_where = f"{where}[{index}]"
        item = strict_object(
            raw, ("pid", "executable", "executable_sha256"), where=child_where
        )
        integer(item["pid"], f"{child_where}.pid", minimum=1, maximum=2**31 - 1)
        bounded_string(item["executable"], f"{child_where}.executable", maximum_bytes=4096)
        sha256_digest(item["executable_sha256"], f"{child_where}.executable_sha256")
        records.append(item)
    pids = [item["pid"] for item in records]
    if len(pids) != len(set(pids)):
        raise ContractError(f"{where} contains duplicate PIDs")
    return tuple(records)


def _exit_records(value: Any, where: str) -> None:
    records = bounded_array(value, where, maximum_items=1024)
    pids = []
    for index, raw in enumerate(records):
        child_where = f"{where}[{index}]"
        item = strict_object(raw, ("pid", "returncode"), where=child_where)
        pids.append(integer(item["pid"], f"{child_where}.pid", minimum=1))
        integer(
            item["returncode"],
            f"{child_where}.returncode",
            minimum=-(2**31),
            maximum=2**31 - 1,
        )
    if len(pids) != len(set(pids)):
        raise ContractError(f"{where} contains duplicate PIDs")


def _validate_request_payload(kind: str, value: Any, where: str) -> Dict[str, Any]:
    if kind not in REQUEST_PAYLOAD_FIELDS:
        raise ContractError(f"unknown lifecycle request kind {kind!r}")
    payload = strict_object(value, REQUEST_PAYLOAD_FIELDS[kind], where=where)
    if kind == "hello":
        if payload["requested_adapter_protocol"] != LIFECYCLE_PROTOCOL_VERSION:
            raise ContractError(
                f"{where}.requested_adapter_protocol must equal "
                f"{LIFECYCLE_PROTOCOL_VERSION!r}"
            )
    elif kind == "prepare_session":
        identifier(payload["run_id"], f"{where}.run_id")
        identifier(payload["session_id"], f"{where}.session_id")
        state_root = bounded_string(payload["state_root"], f"{where}.state_root")
        if not Path(state_root).is_absolute() or ".." in Path(state_root).parts:
            raise ContractError(f"{where}.state_root must be an absolute Benchmark root")
        for field in ("target_sha256", "config_sha256", "model_sha256"):
            sha256_digest(payload[field], f"{where}.{field}")
        identifier(payload["reset_policy"], f"{where}.reset_policy")
    elif kind == "start_target":
        _argv(payload["argv"], f"{where}.argv")
        if not isinstance(payload["config"], dict):
            raise ContractError(f"{where}.config must be an object")
        _bounded_json(payload["config"], f"{where}.config")
        if len(canonical_json_bytes(payload["config"])) > 16 * 1024:
            raise ContractError(f"{where}.config exceeds the 16384-byte bound")
        unique_strings(
            payload["environment_names"],
            f"{where}.environment_names",
            maximum_items=256,
            parse=environment_name,
        )
        integer(
            payload["readiness_deadline_ms"],
            f"{where}.readiness_deadline_ms",
            minimum=1,
            maximum=24 * 60 * 60 * 1000,
        )
    elif kind == "describe_endpoint":
        identifier(payload["target_id"], f"{where}.target_id")
        identifier(payload["session_id"], f"{where}.session_id")
    elif kind == "reset_state":
        integer(payload["reset_ordinal"], f"{where}.reset_ordinal", minimum=1)
        identifier(payload["reset_policy"], f"{where}.reset_policy")
        sha256_digest(
            payload["expected_prior_inventory_sha256"],
            f"{where}.expected_prior_inventory_sha256",
        )
    elif kind == "stop_target":
        identifier(payload["reason"], f"{where}.reason")
        integer(payload["deadline_ms"], f"{where}.deadline_ms", minimum=1)
        integer(
            payload["expected_process_group_leader_pid"],
            f"{where}.expected_process_group_leader_pid",
            minimum=1,
        )
    elif kind == "shutdown":
        identifier(payload["session_id"], f"{where}.session_id")
    return payload


def _validate_response_payload(kind: str, value: Any, where: str) -> Dict[str, Any]:
    if kind not in RESPONSE_PAYLOAD_FIELDS:
        raise ContractError(f"unknown lifecycle response kind {kind!r}")
    payload = strict_object(value, RESPONSE_PAYLOAD_FIELDS[kind], where=where)
    if kind == "hello_ack":
        if payload["adapter_protocol"] != LIFECYCLE_PROTOCOL_VERSION:
            raise ContractError(
                f"{where}.adapter_protocol must equal {LIFECYCLE_PROTOCOL_VERSION!r}"
            )
        for field in ("adapter_id", "adapter_version", "target_family"):
            identifier(payload[field], f"{where}.{field}")
        unique_strings(
            payload["lifecycle_capabilities"],
            f"{where}.lifecycle_capabilities",
            maximum_items=32,
            parse=identifier,
        )
    elif kind == "prepared":
        identifier(payload["run_id"], f"{where}.run_id")
        identifier(payload["session_id"], f"{where}.session_id")
        for field in (
            "target_sha256",
            "config_sha256",
            "model_sha256",
            "initial_state_inventory_sha256",
        ):
            sha256_digest(payload[field], f"{where}.{field}")
        identifier(payload["reset_policy"], f"{where}.reset_policy")
    elif kind == "target_started":
        integer(payload["process_group_leader_pid"], f"{where}.process_group_leader_pid", minimum=1)
        _process_records(payload["child_processes"], f"{where}.child_processes")
        integer(payload["started_at_ns"], f"{where}.started_at_ns")
    elif kind == "endpoint_ready":
        identifier(payload["target_id"], f"{where}.target_id")
        identifier(payload["session_id"], f"{where}.session_id")
        if not isinstance(payload["endpoint"], dict):
            raise ContractError(f"{where}.endpoint must be an object")
        if len(canonical_json_bytes(payload["endpoint"])) > 16 * 1024:
            raise ContractError(f"{where}.endpoint exceeds the 16384-byte bound")
        owner = strict_object(
            payload["listener_owner"], ("pid", "address", "port"), where=f"{where}.listener_owner"
        )
        integer(owner["pid"], f"{where}.listener_owner.pid", minimum=1)
        bounded_string(owner["address"], f"{where}.listener_owner.address", maximum_bytes=64)
        integer(owner["port"], f"{where}.listener_owner.port", minimum=1, maximum=65535)
    elif kind == "state_reset":
        integer(payload["reset_ordinal"], f"{where}.reset_ordinal", minimum=1)
        identifier(payload["reset_policy"], f"{where}.reset_policy")
        _process_records(payload["pre_processes"], f"{where}.pre_processes")
        _process_records(payload["post_processes"], f"{where}.post_processes")
        sha256_digest(payload["prior_inventory_sha256"], f"{where}.prior_inventory_sha256")
        sha256_digest(payload["current_inventory_sha256"], f"{where}.current_inventory_sha256")
    elif kind == "target_stopped":
        integer(payload["process_group_leader_pid"], f"{where}.process_group_leader_pid", minimum=1)
        _exit_records(payload["exit_records"], f"{where}.exit_records")
        if bounded_array(payload["surviving_process_ids"], f"{where}.surviving_process_ids"):
            raise ContractError(f"{where}.surviving_process_ids must be empty for success")
        if bounded_array(payload["surviving_listeners"], f"{where}.surviving_listeners"):
            raise ContractError(f"{where}.surviving_listeners must be empty for success")
    elif kind == "shutdown_ack":
        identifier(payload["session_id"], f"{where}.session_id")
        integer(payload["diagnostic_count"], f"{where}.diagnostic_count", maximum=1024)
        if not boolean(payload["no_live_children"], f"{where}.no_live_children"):
            raise ContractError(f"{where}.no_live_children must be true")
    return payload


def validate_lifecycle_request(value: Any, where: str = "lifecycle request") -> Dict[str, Any]:
    obj = strict_object(value, REQUEST_FIELDS, where=where)
    kind = identifier(obj["kind"], f"{where}.kind")
    if obj["protocol_version"] != LIFECYCLE_PROTOCOL_VERSION:
        raise ContractError(
            f"{where}.protocol_version must equal {LIFECYCLE_PROTOCOL_VERSION!r}"
        )
    identifier(obj["request_id"], f"{where}.request_id")
    _validate_request_payload(kind, obj["payload"], f"{where}.payload")
    return obj


def validate_lifecycle_response(value: Any, where: str = "lifecycle response") -> Dict[str, Any]:
    obj = strict_object(value, RESPONSE_FIELDS, where=where)
    kind = identifier(obj["kind"], f"{where}.kind")
    if obj["protocol_version"] != LIFECYCLE_PROTOCOL_VERSION:
        raise ContractError(
            f"{where}.protocol_version must equal {LIFECYCLE_PROTOCOL_VERSION!r}"
        )
    identifier(obj["request_id"], f"{where}.request_id")
    status = obj["status"]
    if status not in ("ok", "error"):
        raise ContractError(f"{where}.status must be 'ok' or 'error'")
    if status == "ok":
        if obj["error"] is not None:
            raise ContractError(f"{where}.error must be null when status is ok")
        _validate_response_payload(kind, obj["payload"], f"{where}.payload")
    else:
        if obj["payload"] is not None:
            raise ContractError(f"{where}.payload must be null when status is error")
        error = strict_object(
            obj["error"], ("code", "message"), where=f"{where}.error"
        )
        code = identifier(error["code"], f"{where}.error.code")
        if code not in REGISTERED_ERROR_CODES:
            raise ContractError(f"{where}.error.code is not registered")
        bounded_string(error["message"], f"{where}.error.message", maximum_bytes=1024)
    return obj


def lifecycle_request(kind: str, request_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    value = {
        "kind": kind,
        "protocol_version": LIFECYCLE_PROTOCOL_VERSION,
        "request_id": request_id,
        "payload": dict(payload),
    }
    return validate_lifecycle_request(value)


def lifecycle_response(
    kind: str,
    request_id: str,
    *,
    payload: Optional[Mapping[str, Any]] = None,
    error: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    value = {
        "kind": kind,
        "protocol_version": LIFECYCLE_PROTOCOL_VERSION,
        "request_id": request_id,
        "status": "error" if error is not None else "ok",
        "payload": None if error is not None else dict(payload or {}),
        "error": dict(error) if error is not None else None,
    }
    return validate_lifecycle_response(value)


class LifecycleStateMachine:
    def __init__(self) -> None:
        self.state = LifecycleState.SPAWNED
        self._request_ids: Set[str] = set()
        self._response_ids: Set[str] = set()

    def expected_response(self, request: Mapping[str, Any]) -> Tuple[str, LifecycleState]:
        validated = validate_lifecycle_request(request)
        request_id = validated["request_id"]
        if request_id in self._request_ids:
            raise ContractError(f"duplicate lifecycle request_id {request_id!r}")
        key = (self.state, validated["kind"])
        if key not in TRANSITIONS:
            raise ContractError(
                f"lifecycle request {validated['kind']!r} is invalid in state {self.state.value}"
            )
        self._request_ids.add(request_id)
        return TRANSITIONS[key]

    def accept_response(
        self,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        expected_kind: str,
        next_state: LifecycleState,
    ) -> Mapping[str, Any]:
        envelope = strict_object(response, RESPONSE_FIELDS, where="lifecycle response")
        observed_kind = identifier(envelope["kind"], "lifecycle response.kind")
        if observed_kind != expected_kind:
            raise ContractError(
                f"lifecycle response kind must be {expected_kind!r} in state {self.state.value}"
            )
        validated = validate_lifecycle_response(response)
        request_id = validated["request_id"]
        if request_id not in self._request_ids:
            raise ContractError(f"unknown lifecycle response request_id {request_id!r}")
        if request_id in self._response_ids:
            raise ContractError(f"duplicate lifecycle response request_id {request_id!r}")
        if request_id != request["request_id"]:
            raise ContractError("lifecycle response request_id does not echo the request")
        if validated["kind"] != expected_kind:
            raise ContractError(
                f"lifecycle response kind must be {expected_kind!r} in state {self.state.value}"
            )
        self._response_ids.add(request_id)
        if validated["status"] == "error":
            error = validated["error"]
            raise LifecycleRemoteError(error["code"], error["message"])
        self._compare_echoed_identities(request["kind"], request["payload"], validated["payload"])
        self.state = next_state
        return validated["payload"]

    @staticmethod
    def _compare_echoed_identities(
        kind: str, request_payload: Mapping[str, Any], response_payload: Mapping[str, Any]
    ) -> None:
        fields_by_kind = {
            "hello": (("requested_adapter_protocol", "adapter_protocol"),),
            "prepare_session": tuple(
                (field, field)
                for field in (
                    "run_id",
                    "session_id",
                    "target_sha256",
                    "config_sha256",
                    "model_sha256",
                    "reset_policy",
                )
            ),
            "describe_endpoint": (("target_id", "target_id"), ("session_id", "session_id")),
            "reset_state": (
                ("reset_ordinal", "reset_ordinal"),
                ("reset_policy", "reset_policy"),
                ("expected_prior_inventory_sha256", "prior_inventory_sha256"),
            ),
            "stop_target": (
                ("expected_process_group_leader_pid", "process_group_leader_pid"),
            ),
            "shutdown": (("session_id", "session_id"),),
        }
        for request_field, response_field in fields_by_kind.get(kind, ()):
            if request_payload[request_field] != response_payload[response_field]:
                raise ContractError(
                    f"lifecycle response {response_field!r} does not echo "
                    f"request {request_field!r}"
                )


_EOF = object()


class EngineLifecycleClient:
    """Strict one-request/one-response JSONL subprocess client."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeouts: Optional[Mapping[str, float]] = None,
        stderr_limit_bytes: int = 64 * 1024,
        line_limit_bytes: int = MAX_JSONL_LINE_BYTES,
    ) -> None:
        if isinstance(argv, (str, bytes)) or not argv:
            raise ContractError("lifecycle adapter command must be a non-empty argv array")
        self.argv = tuple(
            bounded_string(item, f"adapter argv[{index}]", maximum_bytes=4096)
            for index, item in enumerate(argv)
        )
        integer(stderr_limit_bytes, "stderr_limit_bytes", minimum=1)
        integer(line_limit_bytes, "line_limit_bytes", minimum=1)
        self.stderr_limit_bytes = stderr_limit_bytes
        self.line_limit_bytes = line_limit_bytes
        self.timeouts: Dict[str, float] = {}
        for kind in REQUEST_PAYLOAD_FIELDS:
            timeout = 5.0 if timeouts is None else timeouts.get(kind, 5.0)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise ContractError(f"timeout for {kind} must be numeric")
            if not math.isfinite(float(timeout)) or timeout <= 0:
                raise ContractError(f"timeout for {kind} must be finite and positive")
            self.timeouts[kind] = float(timeout)
        child_env = dict(os.environ if env is None else env)
        for name in list(child_env):
            if name.lower() in {
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            }:
                child_env.pop(name, None)
        try:
            self.process = subprocess.Popen(
                self.argv,
                cwd=str(cwd) if cwd is not None else None,
                env=child_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                bufsize=0,
            )
        except OSError as error:
            raise ContractError(f"cannot spawn lifecycle adapter: {error}") from error
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            raise ContractError("lifecycle adapter pipes were not created")
        self.machine = LifecycleStateMachine()
        self._responses: "queue.Queue[Any]" = queue.Queue()
        self._stderr = bytearray()
        self._stderr_overflow = False
        self._failed = False
        self._sequence = 0
        self._lock = threading.Lock()
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def state(self) -> LifecycleState:
        return self.machine.state

    @property
    def stderr_bytes(self) -> bytes:
        return bytes(self._stderr)

    @property
    def stderr_text(self) -> str:
        return self.stderr_bytes.decode("utf-8", errors="replace")

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            while True:
                line = self.process.stdout.readline(self.line_limit_bytes + 1)
                if not line:
                    self._responses.put(_EOF)
                    return
                self._responses.put(line)
                if len(line) > self.line_limit_bytes or not line.endswith(b"\n"):
                    return
        except (OSError, ValueError) as error:
            self._responses.put(error)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    return
                remaining = self.stderr_limit_bytes - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._stderr_overflow = True
        except (OSError, ValueError):
            self._stderr_overflow = True

    def _failure(self, message: str, error: Optional[BaseException] = None) -> ContractError:
        self._failed = True
        if error is None:
            return ContractError(message)
        return ContractError(message)

    def request(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        request_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Mapping[str, Any]:
        with self._lock:
            if self._failed:
                raise ContractError("lifecycle client is failed closed")
            if self.machine.state == LifecycleState.TERMINATED:
                raise ContractError("lifecycle client is already terminated")
            self._sequence += 1
            current_id = request_id or f"request-{self._sequence:06d}"
            request_value = lifecycle_request(kind, current_id, payload)
            expected_kind, next_state = self.machine.expected_response(request_value)
            raw = encode_jsonl_line(request_value, self.line_limit_bytes)
            assert self.process.stdin is not None
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                self._failed = True
                returncode = self.process.poll()
                raise ContractError(
                    f"lifecycle adapter closed stdin (exit={returncode})"
                ) from error
            wait_seconds = self.timeouts[kind] if timeout is None else timeout
            if (
                isinstance(wait_seconds, bool)
                or not isinstance(wait_seconds, (int, float))
                or not math.isfinite(float(wait_seconds))
                or wait_seconds <= 0
            ):
                self._failed = True
                raise ContractError("lifecycle request timeout must be finite and positive")
            try:
                observed = self._responses.get(timeout=float(wait_seconds))
            except queue.Empty as error:
                self._failed = True
                raise ContractError(f"lifecycle request {kind!r} timed out") from error
            if observed is _EOF:
                self._failed = True
                returncode = self.process.poll()
                raise ContractError(
                    f"lifecycle adapter reached EOF before response (exit={returncode})"
                )
            if isinstance(observed, BaseException):
                self._failed = True
                raise ContractError(f"cannot read lifecycle response: {observed}") from observed
            try:
                response = decode_jsonl_line(
                    observed,
                    max_bytes=self.line_limit_bytes,
                    where="lifecycle response line",
                )
                result = self.machine.accept_response(
                    request_value, response, expected_kind, next_state
                )
            except LifecycleRemoteError:
                raise
            except ContractError:
                self._failed = True
                raise
            if self._stderr_overflow:
                self._failed = True
                raise ContractError(
                    f"lifecycle adapter stderr exceeds the {self.stderr_limit_bytes}-byte bound"
                )
            if next_state == LifecycleState.TERMINATED:
                try:
                    returncode = self.process.wait(timeout=float(wait_seconds))
                except subprocess.TimeoutExpired as error:
                    self._failed = True
                    raise ContractError("lifecycle adapter did not exit after shutdown") from error
                self._stderr_thread.join(timeout=min(float(wait_seconds), 1.0))
                if self._stderr_overflow:
                    self._failed = True
                    raise ContractError(
                        f"lifecycle adapter stderr exceeds the {self.stderr_limit_bytes}-byte bound"
                    )
                if returncode != 0:
                    self._failed = True
                    raise ContractError(
                        f"lifecycle adapter exited nonzero after shutdown: {returncode}"
                    )
            elif self.process.poll() not in (None, 0):
                self._failed = True
                raise ContractError(
                    f"lifecycle adapter exited nonzero: {self.process.returncode}"
                )
            return result

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def __enter__(self) -> "EngineLifecycleClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
