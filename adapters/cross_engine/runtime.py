from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from turnvector_benchmark.evidence import sha256_file
from turnvector_benchmark.cross_engine.adapters import registration_for
from turnvector_benchmark.cross_engine.contracts import (
    LIFECYCLE_PROTOCOL_VERSION,
    MAX_JSONL_LINE_BYTES,
    decode_jsonl_line,
    encode_jsonl_line,
)
from turnvector_benchmark.cross_engine.lifecycle import (
    TRANSITIONS,
    LifecycleState,
    lifecycle_response,
    validate_lifecycle_request,
)


MAX_DIAGNOSTIC_BYTES = 16384


def _inventory(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise RuntimeError("state root contains a symlink")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        if path.is_file():
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        elif not path.is_dir():
            raise RuntimeError("state root contains a special file")
    return digest.hexdigest()


def _write(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(encode_jsonl_line(value))
    sys.stdout.buffer.flush()


def _process_record(pid: int, executable: str) -> Mapping[str, Any]:
    path = Path(executable)
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("registered target executable is not a regular file")
    return {"pid": pid, "executable": str(path), "executable_sha256": sha256_file(path)}


class LifecycleRuntime:
    def __init__(self, command_id: str) -> None:
        self.registration = registration_for(command_id)
        self.command_id = command_id
        self.state = LifecycleState.SPAWNED
        self.run_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.state_root: Optional[Path] = None
        self.identities: Dict[str, str] = {}
        self.reset_policy: Optional[str] = None
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.argv: Tuple[str, ...] = ()
        self.config: Mapping[str, Any] = {}
        self._stderr_path: Optional[Path] = None
        self._stdout_path: Optional[Path] = None

    def dispatch(self, request: Mapping[str, Any]) -> Tuple[str, Mapping[str, Any]]:
        kind = request["kind"]
        payload = request["payload"]
        transition = TRANSITIONS.get((self.state, kind))
        if transition is None:
            raise RuntimeError(f"request {kind!r} is invalid in state {self.state.value}")
        response_kind, next_state = transition
        if kind == "hello":
            result = {
                "adapter_protocol": LIFECYCLE_PROTOCOL_VERSION,
                "adapter_id": self.registration.adapter_id,
                "adapter_version": self.registration.adapter_version,
                "target_family": self.registration.engine_family,
                "lifecycle_capabilities": ["process_group_cleanup", "reset_state"],
            }
        elif kind == "prepare_session":
            root = Path(payload["state_root"])
            if not root.is_absolute() or not root.is_dir() or root.is_symlink():
                raise RuntimeError("state_root must be an existing absolute directory")
            self.run_id = payload["run_id"]
            self.session_id = payload["session_id"]
            self.state_root = root
            self.identities = {
                field: payload[field]
                for field in ("target_sha256", "config_sha256", "model_sha256")
            }
            self.reset_policy = payload["reset_policy"]
            result = {
                "run_id": self.run_id,
                "session_id": self.session_id,
                **self.identities,
                "reset_policy": self.reset_policy,
                "initial_state_inventory_sha256": _inventory(root),
            }
        elif kind == "start_target":
            assert self.state_root is not None
            config = payload["config"]
            if not isinstance(config, dict) or set(config) != {"command_id", "resolved_argv", "endpoint"}:
                raise RuntimeError("config must be the closed registered adapter projection")
            if config["command_id"] != self.command_id:
                raise RuntimeError("config command_id does not match this registered adapter")
            argv = tuple(config["resolved_argv"])
            if not argv or list(argv) != payload["argv"]:
                raise RuntimeError("argv does not match the Benchmark-resolved registered command")
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(self.state_root),
                "TMPDIR": str(self.state_root),
            }
            for name in payload["environment_names"]:
                if name in os.environ:
                    environment[name] = os.environ[name]
            self._stdout_path = self.state_root / "target.stdout"
            self._stderr_path = self.state_root / "target.stderr"
            stdout_handle = self._stdout_path.open("xb")
            stderr_handle = self._stderr_path.open("xb")
            try:
                self.process = subprocess.Popen(
                    argv,
                    cwd=str(self.state_root),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    shell=False,
                    start_new_session=True,
                )
            finally:
                stdout_handle.close()
                stderr_handle.close()
            self.argv = argv
            self.config = config
            result = {
                "process_group_leader_pid": self.process.pid,
                "child_processes": [_process_record(self.process.pid, argv[0])],
                "started_at_ns": time.monotonic_ns(),
            }
        elif kind == "describe_endpoint":
            assert self.process is not None
            if self.process.poll() is not None:
                raise RuntimeError("target exited before endpoint description")
            endpoint = self.config["endpoint"]
            if not isinstance(endpoint, dict):
                raise RuntimeError("endpoint config must be an object")
            try:
                host = self.argv[self.argv.index("--host") + 1]
                port = int(self.argv[self.argv.index("--port") + 1])
            except (ValueError, IndexError) as error:
                raise RuntimeError("registered argv lacks loopback host/port") from error
            endpoint_ready = dict(endpoint)
            endpoint_ready["process_ids"] = [self.process.pid]
            result = {
                "target_id": payload["target_id"],
                "session_id": payload["session_id"],
                "endpoint": endpoint_ready,
                "listener_owner": {"pid": self.process.pid, "address": host, "port": port},
            }
        elif kind == "reset_state":
            assert self.state_root is not None and self.process is not None
            if payload["reset_policy"] != self.reset_policy:
                raise RuntimeError("reset policy changed after prepare_session")
            prior = _inventory(self.state_root)
            if payload["expected_prior_inventory_sha256"] != prior:
                raise RuntimeError("expected prior state inventory does not match")
            record = _process_record(self.process.pid, self.argv[0])
            result = {
                "reset_ordinal": payload["reset_ordinal"],
                "reset_policy": payload["reset_policy"],
                "pre_processes": [record],
                "post_processes": [record],
                "prior_inventory_sha256": prior,
                "current_inventory_sha256": _inventory(self.state_root),
            }
        elif kind == "stop_target":
            result = self.stop(payload["expected_process_group_leader_pid"])
        elif kind == "shutdown":
            result = {
                "session_id": payload["session_id"],
                "diagnostic_count": int(bool(self._stderr_tail())),
                "no_live_children": True,
            }
        else:  # pragma: no cover - TRANSITIONS and lifecycle validator close this set.
            raise RuntimeError("unsupported lifecycle request")
        self.state = next_state
        return response_kind, result

    def _stderr_tail(self) -> str:
        if self._stderr_path is None or not self._stderr_path.is_file():
            return ""
        data = self._stderr_path.read_bytes()[-MAX_DIAGNOSTIC_BYTES:]
        # Adapter diagnostics are counted but never returned: inherited secret
        # values could have been echoed by the target.
        return data.decode("utf-8", errors="replace")

    def stop(self, expected_leader: int) -> Mapping[str, Any]:
        assert self.process is not None
        pid = self.process.pid
        if expected_leader != pid:
            raise RuntimeError("stop request names another process group")
        if self.process.poll() is None:
            os.killpg(pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                os.killpg(pid, signal.SIGKILL)
                self.process.wait(timeout=5.0)
        return {
            "process_group_leader_pid": pid,
            "exit_records": [{"pid": pid, "returncode": int(self.process.returncode)}],
            "surviving_process_ids": [],
            "surviving_listeners": [],
        }


def _error_code(kind: str) -> str:
    return {
        "hello": "invalid_request",
        "prepare_session": "prepare_failed",
        "start_target": "start_failed",
        "describe_endpoint": "endpoint_unavailable",
        "reset_state": "reset_failed",
        "stop_target": "stop_failed",
        "shutdown": "shutdown_failed",
    }.get(kind, "invalid_request")


def main(command_id: Optional[str] = None) -> int:
    if command_id is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--command-id", required=True)
        command_id = parser.parse_args().command_id
    runtime = LifecycleRuntime(command_id)
    seen = set()
    while runtime.state != LifecycleState.TERMINATED:
        line = sys.stdin.buffer.readline(MAX_JSONL_LINE_BYTES + 1)
        if not line:
            return 2
        request_id = "unknown"
        kind = "unknown"
        try:
            request = validate_lifecycle_request(
                decode_jsonl_line(line, where="lifecycle request line")
            )
            request_id = request["request_id"]
            kind = request["kind"]
            if request_id in seen:
                raise RuntimeError("duplicate request_id")
            seen.add(request_id)
            response_kind, payload = runtime.dispatch(request)
            _write(lifecycle_response(response_kind, request_id, payload=payload))
        except Exception as error:
            transition = TRANSITIONS.get((runtime.state, kind))
            response_kind = transition[0] if transition is not None else "hello_ack"
            _write(
                lifecycle_response(
                    response_kind,
                    request_id if request_id != "unknown" else "unknown",
                    error={"code": _error_code(kind), "message": str(error)[:1024]},
                )
            )
            # A malformed control envelope cannot be correlated safely.
            if request_id == "unknown":
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
