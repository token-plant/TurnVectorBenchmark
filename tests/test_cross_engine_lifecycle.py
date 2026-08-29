from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.contracts import (
    LIFECYCLE_PROTOCOL_VERSION,
    MAX_JSONL_LINE_BYTES,
    decode_jsonl_line,
    encode_jsonl_line,
)
from turnvector_benchmark.cross_engine.lifecycle import (
    EngineLifecycleClient,
    LifecycleRemoteError,
    LifecycleState,
    LifecycleStateMachine,
    lifecycle_request,
    lifecycle_response,
)


ROOT = Path(__file__).resolve().parent.parent
ADAPTER = ROOT / "tests" / "fixtures" / "cross_engine" / "lifecycle_adapter.py"
DIGEST = "a" * 64


def hello_payload():
    return {"requested_adapter_protocol": LIFECYCLE_PROTOCOL_VERSION}


def prepared_payload(root: Path):
    return {
        "run_id": "run-v1",
        "session_id": "session-v1",
        "state_root": str(root),
        "target_sha256": DIGEST,
        "config_sha256": DIGEST,
        "model_sha256": DIGEST,
        "reset_policy": "fresh-process-fresh-state-root",
    }


class BoundedJSONLTests(unittest.TestCase):
    def test_compact_utf8_round_trip_has_exact_final_lf(self) -> None:
        value = {"kind": "fixture", "payload": "世界"}
        raw = encode_jsonl_line(value)
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertIn("世界".encode("utf-8"), raw)
        self.assertEqual(decode_jsonl_line(raw), value)

    def test_malformed_duplicate_nonfinite_and_oversized_lines_fail_closed(self) -> None:
        cases = (
            b'{"a":1}',
            b'{"a":1}\r\n',
            b'{"a":1}\n\n',
            b'{"a":1,"a":2}\n',
            b'{"a":NaN}\n',
            b'\xff\n',
            b'[]\n',
            b'x' * (MAX_JSONL_LINE_BYTES + 1),
        )
        for raw in cases:
            with self.subTest(raw=raw[:24]):
                with self.assertRaises(ContractError):
                    decode_jsonl_line(raw)


class LifecycleStateMachineTests(unittest.TestCase):
    def test_exact_transitions_and_repeated_ready_reset(self) -> None:
        machine = LifecycleStateMachine()
        request = lifecycle_request("hello", "r-1", hello_payload())
        expected, next_state = machine.expected_response(request)
        payload = {
            "adapter_protocol": LIFECYCLE_PROTOCOL_VERSION,
            "adapter_id": "fixture",
            "adapter_version": "v1",
            "target_family": "fixture",
            "lifecycle_capabilities": ["reset_state"],
        }
        machine.accept_response(
            request,
            lifecycle_response(expected, "r-1", payload=payload),
            expected,
            next_state,
        )
        self.assertEqual(machine.state, LifecycleState.NEGOTIATED)
        with self.assertRaisesRegex(ContractError, "invalid in state"):
            machine.expected_response(lifecycle_request("hello", "r-2", hello_payload()))

    def test_duplicate_request_unknown_fields_and_wrong_response_fail(self) -> None:
        machine = LifecycleStateMachine()
        request = lifecycle_request("hello", "same-id", hello_payload())
        machine.expected_response(request)
        with self.assertRaisesRegex(ContractError, "duplicate"):
            machine.expected_response(request)
        invalid = dict(request)
        invalid["extra"] = True
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            LifecycleStateMachine().expected_response(invalid)


class LifecycleSubprocessTests(unittest.TestCase):
    def client(self, mode: str = "normal", **kwargs) -> EngineLifecycleClient:
        client = EngineLifecycleClient(
            [sys.executable, "-B", str(ADAPTER), "--mode", mode],
            cwd=ROOT,
            **kwargs,
        )
        self.addCleanup(client.close)
        return client

    def negotiate(self, client: EngineLifecycleClient) -> None:
        client.request("hello", hello_payload())

    def test_fixture_adapter_completes_exact_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            self.negotiate(client)
            client.request("prepare_session", prepared_payload(Path(directory)))
            started = client.request(
                "start_target",
                {
                    "argv": ["fixture-server", "--model", "fixture-model"],
                    "config": {"temperature": 0},
                    "environment_names": [],
                    "readiness_deadline_ms": 1000,
                },
            )
            endpoint = client.request(
                "describe_endpoint",
                {"target_id": "target-v1", "session_id": "session-v1"},
            )
            self.assertEqual(endpoint["endpoint"]["transport"], "http")
            for ordinal in (1, 2):
                client.request(
                    "reset_state",
                    {
                        "reset_ordinal": ordinal,
                        "reset_policy": "fresh-process-fresh-state-root",
                        "expected_prior_inventory_sha256": DIGEST,
                    },
                )
                self.assertEqual(client.state, LifecycleState.READY)
            client.request(
                "stop_target",
                {
                    "reason": "completed",
                    "deadline_ms": 1000,
                    "expected_process_group_leader_pid": started[
                        "process_group_leader_pid"
                    ],
                },
            )
            client.request("shutdown", {"session_id": "session-v1"})
            self.assertEqual(client.state, LifecycleState.TERMINATED)
            self.assertEqual(client.process.returncode, 0)

    def test_started_can_stop_for_startup_failure_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.client()
            self.negotiate(client)
            client.request("prepare_session", prepared_payload(Path(directory)))
            started = client.request(
                "start_target",
                {
                    "argv": ["fixture-server"],
                    "config": {},
                    "environment_names": [],
                    "readiness_deadline_ms": 1000,
                },
            )
            client.request(
                "stop_target",
                {
                    "reason": "startup-failed",
                    "deadline_ms": 1000,
                    "expected_process_group_leader_pid": started[
                        "process_group_leader_pid"
                    ],
                },
            )
            self.assertEqual(client.state, LifecycleState.STOPPED)

    def test_wrong_kind_malformed_oversized_eof_timeout_and_stderr_fail_closed(self) -> None:
        cases = (
            ("wrong-kind", {}, "kind"),
            ("malformed-json", {}, "valid JSON"),
            ("oversized-line", {}, "exceeds"),
            ("crash", {}, "EOF"),
            ("timeout", {"timeouts": {"hello": 0.05}}, "timed out"),
            ("stderr-overflow", {"stderr_limit_bytes": 1024}, "stderr exceeds"),
        )
        for mode, kwargs, message in cases:
            with self.subTest(mode=mode):
                client = self.client(mode, **kwargs)
                with self.assertRaisesRegex(ContractError, message):
                    client.request("hello", hello_payload())
                with self.assertRaisesRegex(ContractError, "failed closed"):
                    client.request("hello", hello_payload())

    def test_valid_remote_error_preserves_state_but_does_not_transition(self) -> None:
        client = self.client("remote-error")
        with self.assertRaises(LifecycleRemoteError) as caught:
            client.request("hello", hello_payload())
        self.assertEqual(caught.exception.code, "unsupported")
        self.assertEqual(client.state, LifecycleState.SPAWNED)

    def test_identity_mismatch_and_orphan_cleanup_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.client("identity-mismatch")
            self.negotiate(client)
            with self.assertRaisesRegex(ContractError, "does not echo"):
                client.request("prepare_session", prepared_payload(Path(directory)))

        with tempfile.TemporaryDirectory() as directory:
            client = self.client("orphan")
            self.negotiate(client)
            client.request("prepare_session", prepared_payload(Path(directory)))
            started = client.request(
                "start_target",
                {
                    "argv": ["fixture-server"],
                    "config": {},
                    "environment_names": [],
                    "readiness_deadline_ms": 1000,
                },
            )
            client.request(
                "describe_endpoint",
                {"target_id": "target-v1", "session_id": "session-v1"},
            )
            with self.assertRaisesRegex(ContractError, "must be empty"):
                client.request(
                    "stop_target",
                    {
                        "reason": "completed",
                        "deadline_ms": 1000,
                        "expected_process_group_leader_pid": started[
                            "process_group_leader_pid"
                        ],
                    },
                )


if __name__ == "__main__":
    unittest.main()
