from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from turnvector_benchmark.cross_engine.campaign import freeze_campaign
from turnvector_benchmark.cross_engine.controller import (
    CrossEngineController,
    RetryableAttemptError,
    decide_preflight,
)


class FakeLifecycle:
    instances = []
    fail_cleanup = False

    def __init__(self, argv) -> None:
        self.argv = tuple(argv)
        self.requests = []
        self.closed = False
        FakeLifecycle.instances.append(self)

    def request(self, kind: str, payload: Mapping[str, Any]):
        self.requests.append((kind, dict(payload)))
        if kind == "hello":
            return {}
        if kind == "prepare_session":
            return {"initial_state_inventory_sha256": "0" * 64}
        if kind == "start_target":
            return {
                "process_group_leader_pid": 700,
                "child_processes": [
                    {
                        "pid": 700,
                        "executable": payload["argv"][0],
                        "executable_sha256": "a" * 64,
                    }
                ],
                "started_at_ns": 1,
            }
        if kind == "describe_endpoint":
            return {
                "target_id": payload["target_id"],
                "session_id": payload["session_id"],
                "endpoint": {
                    "base_url": "http://127.0.0.1:31418/v1",
                    "protocol_family": "openai-compatible",
                },
                "listener_owner": {"pid": 700, "address": "127.0.0.1", "port": 31418},
            }
        if kind == "stop_target" and self.fail_cleanup:
            raise RuntimeError("cleanup fixture failure")
        if kind in {"stop_target", "shutdown"}:
            return {}
        raise AssertionError(kind)

    def close(self) -> None:
        self.closed = True


class FakeCollection:
    def as_dict(self):
        return {"samples": [{"monotonic_ns": 1, "total_rss_bytes": 100}]}


class FakeCollector:
    def __init__(self, process_ids) -> None:
        self.process_ids = tuple(process_ids)
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> FakeCollection:
        self.started = False
        return FakeCollection()


class CrossEngineControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLifecycle.instances = []
        FakeLifecycle.fail_cleanup = False

    def output_path(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / "evidence"

    def campaign(self, *, retryable=()):
        return freeze_campaign(
            campaign_id="campaign-v1",
            cases=[
                {
                    "scenario_id": "single-client-streaming",
                    "case_id": "short",
                    "required_capabilities": [],
                }
            ],
            target_ids=["mlx-lm"],
            repetition_count=1,
            retryable_reason_codes=retryable,
        )

    @staticmethod
    def target(capabilities=None):
        return {
            "engine_family": "mlx-lm",
            "command_id": "mlx-lm-openai-server",
            "adapter_arguments": {
                "python_executable": "/usr/bin/python3",
                "host": "127.0.0.1",
                "port": 31418,
                "model": "/models/qwen",
            },
            "endpoint": {
                "protocol_family": "openai-compatible",
                "protocol_version": "turnvector.benchmark.openai-serving.v1",
                "transport": "http",
                "base_url": "http://127.0.0.1:31418/v1",
                "api_flavor": "chat_completions",
                "stream_format": "sse-data-json",
                "model_ids": ["qwen"],
                "process_ids": [700],
                "capability_report_sha256": "b" * 64,
                "authentication_env_var": None,
            },
            "capabilities": capabilities
            or {"openai_chat_completions": True, "streaming_sse": True, "usage_accounting_or_benchmark_tokenizer": True},
            "environment_names": [],
        }

    @staticmethod
    def probe(target, cell):
        return {
            "capabilities": {
                "openai_chat_completions": True,
                "streaming_sse": True,
                "usage_accounting_or_benchmark_tokenizer": True,
            },
            "profile_compatible": True,
            "environment_available": True,
        }

    def test_preflight_distinguishes_unsupported_and_contract_mismatch(self) -> None:
        unsupported = decide_preflight(
            required_capabilities=["mtp"],
            declared_capabilities={
                "openai_chat_completions": True,
                "streaming_sse": True,
                "usage_accounting_or_benchmark_tokenizer": True,
                "mtp": False,
            },
            probed_capabilities={
                "openai_chat_completions": True,
                "streaming_sse": True,
                "usage_accounting_or_benchmark_tokenizer": True,
            },
        )
        self.assertEqual(unsupported.capability_status, "capability_unsupported")
        self.assertEqual(unsupported.contract_status, "valid")

        mismatch = decide_preflight(
            required_capabilities=[],
            declared_capabilities={
                "openai_chat_completions": True,
                "streaming_sse": True,
                "usage_accounting_or_benchmark_tokenizer": True,
            },
            probed_capabilities={
                "openai_chat_completions": True,
                "streaming_sse": False,
                "usage_accounting_or_benchmark_tokenizer": True,
            },
        )
        self.assertEqual(mismatch.contract_status, "invalid")
        self.assertEqual(mismatch.reason_code, "capability_declaration_probe_mismatch")

    def test_controller_runs_common_scenario_with_benchmark_owned_evidence(self) -> None:
        seen = []

        def execute(*, cell, endpoint, collector):
            seen.append((cell.case_id, endpoint["base_url"], collector.process_ids))
            return {"request_count": 1, "successful_request_count": 1}

        controller = CrossEngineController(
            campaign=self.campaign(),
            targets={"mlx-lm": self.target()},
            output_root=self.output_path(),
            scenario_executor=execute,
            capability_probe=self.probe,
            lifecycle_factory=FakeLifecycle,
            collector_factory=FakeCollector,
        )
        result = controller.run()
        self.assertEqual(seen, [("short", "http://127.0.0.1:31418/v1", (700,))])
        cell = result.cells[0]
        self.assertEqual(cell.contract_status, "valid")
        self.assertEqual(cell.capability_status, "supported")
        self.assertEqual(cell.execution_status, "completed")
        self.assertEqual(cell.evidence_status, "publishable")
        self.assertIn("host_process_evidence", cell.observations)
        self.assertEqual(
            [kind for kind, _ in FakeLifecycle.instances[0].requests],
            [
                "hello",
                "prepare_session",
                "start_target",
                "describe_endpoint",
                "stop_target",
                "shutdown",
            ],
        )
        self.assertTrue(FakeLifecycle.instances[0].closed)
        start_config = FakeLifecycle.instances[0].requests[2][1]["config"]
        self.assertEqual(
            set(start_config), {"command_id", "resolved_argv", "endpoint"}
        )
        self.assertEqual(
            start_config["resolved_argv"],
            FakeLifecycle.instances[0].requests[2][1]["argv"],
        )

    def test_formal_capability_dispositions_are_accepted(self) -> None:
        target = self.target()
        target["capabilities"] = {
            name: {"status": "supported", "reason_code": None}
            for name in (
                "openai_chat_completions",
                "streaming_sse",
                "usage_accounting_or_benchmark_tokenizer",
            )
        }

        def formal_probe(target, cell):
            return {"capabilities": target["capabilities"]}

        controller = CrossEngineController(
            campaign=self.campaign(),
            targets={"mlx-lm": target},
            output_root=self.output_path(),
            scenario_executor=lambda **kwargs: {"request_count": 1},
            capability_probe=formal_probe,
            lifecycle_factory=FakeLifecycle,
            collector_factory=FakeCollector,
        )
        result = controller.run()
        self.assertEqual(result.cells[0].capability_status, "supported")
        self.assertEqual(result.cells[0].execution_status, "completed")

    def test_cleanup_diagnostic_is_retained_after_successful_scenario(self) -> None:
        FakeLifecycle.fail_cleanup = True
        controller = CrossEngineController(
            campaign=self.campaign(),
            targets={"mlx-lm": self.target()},
            output_root=self.output_path(),
            scenario_executor=lambda **kwargs: {"request_count": 1},
            capability_probe=self.probe,
            lifecycle_factory=FakeLifecycle,
            collector_factory=FakeCollector,
        )
        result = controller.run()
        self.assertEqual(result.cells[0].execution_status, "completed")
        self.assertEqual(
            [item["kind"] for item in result.cells[0].diagnostics],
            ["lifecycle_cleanup_failed"],
        )
        self.assertIn("cleanup fixture failure", result.cells[0].diagnostics[0]["message"])

    def test_unsupported_target_is_explicit_and_never_launched(self) -> None:
        target = self.target(
            {"openai_chat_completions": False, "streaming_sse": True, "usage_accounting_or_benchmark_tokenizer": True}
        )
        controller = CrossEngineController(
            campaign=self.campaign(),
            targets={"mlx-lm": target},
            output_root=self.output_path(),
            scenario_executor=lambda **kwargs: self.fail("must not execute"),
            capability_probe=self.probe,
            lifecycle_factory=FakeLifecycle,
            collector_factory=FakeCollector,
        )
        result = controller.run()
        self.assertEqual(result.cells[0].capability_status, "profile_incompatible")
        self.assertEqual(result.cells[0].execution_status, "not_started")
        self.assertEqual(FakeLifecycle.instances, [])
        self.assertEqual(result.attempts_path.read_text(), "")

    def test_formal_target_manifest_resolves_registered_endpoint_projection(self) -> None:
        root = Path(__file__).resolve().parents[1]
        target = json.loads((root / "targets" / "mlx-lm-openai-v1.json").read_text())
        campaign = freeze_campaign(
            campaign_id="campaign-v1",
            cases=[
                {
                    "scenario_id": "single-client-streaming",
                    "case_id": "short",
                    "required_capabilities": [
                        "openai_chat_completions",
                        "streaming_sse",
                    ],
                }
            ],
            target_ids=[target["id"]],
            repetition_count=1,
        )
        controller = CrossEngineController(
            campaign=campaign,
            targets={target["id"]: target},
            output_root=self.output_path(),
            scenario_executor=lambda **kwargs: {"request_count": 1},
            capability_probe=lambda target, cell: {
                "capabilities": target["capabilities"]
            },
            lifecycle_factory=FakeLifecycle,
            collector_factory=FakeCollector,
            runtime_bindings={
                target["id"]: {
                    "python_executable": "/usr/bin/python3",
                    "target_checkout": "/tmp/mlx-lm",
                    "model_root": "/tmp/model",
                    "port": 31418,
                }
            },
        )
        result = controller.run()
        self.assertEqual(result.cells[0].execution_status, "completed")
        start_payload = dict(FakeLifecycle.instances[0].requests)["start_target"]
        self.assertEqual(
            set(start_payload["config"]),
            {"command_id", "resolved_argv", "endpoint"},
        )
        endpoint = start_payload["config"]["endpoint"]
        self.assertEqual(endpoint["base_url"], "http://127.0.0.1:31418/v1")
        self.assertEqual(endpoint["model_ids"], [target["model"]["id"]])
        self.assertNotIn("output_bound_field", endpoint)

    def test_only_predeclared_invalid_attempt_is_retried_and_retained(self) -> None:
        calls = 0

        def execute(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryableAttemptError(
                    "environment_invalid", "host_load_transient", "load changed"
                )
            return {"request_count": 1}

        controller = CrossEngineController(
            campaign=self.campaign(retryable=("host_load_transient",)),
            targets={"mlx-lm": self.target()},
            output_root=self.output_path(),
            scenario_executor=execute,
            capability_probe=self.probe,
            lifecycle_factory=FakeLifecycle,
            collector_factory=FakeCollector,
        )
        result = controller.run()
        rows = [json.loads(line) for line in result.attempts_path.read_text().splitlines()]
        self.assertEqual([row["status"] for row in rows], ["environment_invalid", "completed"])
        self.assertEqual(rows[1]["retry_of"], 0)
        self.assertEqual(result.cells[0].primary_attempt_ordinal, 1)
        self.assertEqual(len(FakeLifecycle.instances), 2)


if __name__ == "__main__":
    unittest.main()
