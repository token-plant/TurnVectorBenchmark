"""Synthetic lifecycle adapter used only by cross-engine contract tests."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from turnvector_benchmark.cross_engine.contracts import (
    LIFECYCLE_PROTOCOL_VERSION,
    decode_jsonl_line,
    encode_jsonl_line,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _response(kind: str, request_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "kind": kind,
        "protocol_version": LIFECYCLE_PROTOCOL_VERSION,
        "request_id": request_id,
        "status": "ok",
        "payload": dict(payload),
        "error": None,
    }


def _payload(request: Mapping[str, Any]) -> Mapping[str, Any]:
    kind = request["kind"]
    payload = request["payload"]
    pid = os.getpid()
    if kind == "hello":
        return {
            "adapter_protocol": LIFECYCLE_PROTOCOL_VERSION,
            "adapter_id": "synthetic-openai-fixture",
            "adapter_version": "v1",
            "target_family": "fixture",
            "lifecycle_capabilities": ["reset_state"],
        }
    if kind == "prepare_session":
        return {
            "run_id": payload["run_id"],
            "session_id": payload["session_id"],
            "target_sha256": payload["target_sha256"],
            "config_sha256": payload["config_sha256"],
            "model_sha256": payload["model_sha256"],
            "reset_policy": payload["reset_policy"],
            "initial_state_inventory_sha256": DIGEST_A,
        }
    if kind == "start_target":
        return {
            "process_group_leader_pid": pid,
            "child_processes": [
                {
                    "pid": pid,
                    "executable": sys.executable,
                    "executable_sha256": DIGEST_B,
                }
            ],
            "started_at_ns": time.monotonic_ns(),
        }
    if kind == "describe_endpoint":
        return {
            "target_id": payload["target_id"],
            "session_id": payload["session_id"],
            "endpoint": {
                "protocol_family": "openai-compatible",
                "protocol_version": "turnvector.benchmark.openai-serving.v1",
                "transport": "http",
                "base_url": "http://127.0.0.1:31418/v1",
                "api_flavor": "chat_completions",
                "stream_format": "sse-data-json",
                "model_ids": ["fixture-model"],
                "process_ids": [pid],
                "capability_report_sha256": DIGEST_A,
                "authentication_env_var": None,
            },
            "listener_owner": {"pid": pid, "address": "127.0.0.1", "port": 31418},
        }
    if kind == "reset_state":
        process = {
            "pid": pid,
            "executable": sys.executable,
            "executable_sha256": DIGEST_B,
        }
        return {
            "reset_ordinal": payload["reset_ordinal"],
            "reset_policy": payload["reset_policy"],
            "pre_processes": [process],
            "post_processes": [process],
            "prior_inventory_sha256": payload["expected_prior_inventory_sha256"],
            "current_inventory_sha256": DIGEST_A,
        }
    if kind == "stop_target":
        return {
            "process_group_leader_pid": payload["expected_process_group_leader_pid"],
            "exit_records": [{"pid": pid, "returncode": 0}],
            "surviving_process_ids": [],
            "surviving_listeners": [],
        }
    if kind == "shutdown":
        return {
            "session_id": payload["session_id"],
            "diagnostic_count": 0,
            "no_live_children": True,
        }
    raise AssertionError(kind)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()
    sequence = 0
    while True:
        line = sys.stdin.buffer.readline(64 * 1024 + 1)
        if not line:
            return 0
        sequence += 1
        request = decode_jsonl_line(line)
        kind = request["kind"]
        if args.mode == "timeout" and sequence == 1:
            time.sleep(2.0)
            continue
        if args.mode == "crash" and sequence == 1:
            return 7
        if args.mode == "malformed-json" and sequence == 1:
            sys.stdout.buffer.write(b"{not-json}\n")
            sys.stdout.buffer.flush()
            continue
        if args.mode == "oversized-line" and sequence == 1:
            sys.stdout.buffer.write(b"x" * (64 * 1024 + 1))
            sys.stdout.buffer.flush()
            continue
        if args.mode == "stderr-overflow" and sequence == 1:
            sys.stderr.buffer.write(b"x" * (80 * 1024))
            sys.stderr.buffer.flush()
        response_kind = {
            "hello": "hello_ack",
            "prepare_session": "prepared",
            "start_target": "target_started",
            "describe_endpoint": "endpoint_ready",
            "reset_state": "state_reset",
            "stop_target": "target_stopped",
            "shutdown": "shutdown_ack",
        }[kind]
        if args.mode == "remote-error" and sequence == 1:
            response = {
                "kind": response_kind,
                "protocol_version": LIFECYCLE_PROTOCOL_VERSION,
                "request_id": request["request_id"],
                "status": "error",
                "payload": None,
                "error": {"code": "unsupported", "message": "fixture rejection"},
            }
        else:
            payload = dict(_payload(request))
            if args.mode == "identity-mismatch" and kind == "prepare_session":
                payload["session_id"] = "wrong-session"
            if args.mode == "orphan" and kind == "stop_target":
                payload["surviving_process_ids"] = [999]
            response = _response(response_kind, request["request_id"], payload)
            if args.mode == "wrong-kind" and sequence == 1:
                response["kind"] = "prepared"
        raw = encode_jsonl_line(response)
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        if args.mode == "duplicate-response" and sequence == 1:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
        if kind == "shutdown":
            return 7 if args.mode == "nonzero-shutdown" else 0


if __name__ == "__main__":
    raise SystemExit(main())
