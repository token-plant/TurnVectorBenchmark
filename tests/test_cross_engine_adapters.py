from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.adapters import (
    ADAPTER_REGISTRY,
    build_target_argv,
    lifecycle_adapter_argv,
    registered_command_ids,
    registration_for,
    resolve_target_adapter,
)
from turnvector_benchmark.cross_engine.collectors import (
    ListenerRecord,
    ProcessRecord,
    identities_stable,
    validate_process_ownership,
)

ROOT = Path(__file__).resolve().parent.parent


class CrossEngineAdapterTests(unittest.TestCase):
    def test_registry_is_fixed_to_four_first_party_targets(self) -> None:
        self.assertEqual(
            registered_command_ids(),
            (
                "ax-engine-openai-server",
                "llama-cpp-openai-server",
                "mlx-lm-openai-server",
                "turnvector-openai-server",
            ),
        )
        self.assertEqual(
            {entry.engine_family for entry in ADAPTER_REGISTRY.values()},
            {"turnvector", "ax-engine", "mlx-lm", "llama.cpp"},
        )
        with self.assertRaisesRegex(ContractError, "unregistered"):
            registration_for("manifest.plugin.module")

    def test_declarative_arguments_build_shell_free_registered_argv(self) -> None:
        argv = build_target_argv(
            "mlx-lm-openai-server",
            {
                "python_executable": "/usr/bin/python3",
                "host": "127.0.0.1",
                "port": 31418,
                "model": "/models/qwen",
                "trust_remote_code": False,
            },
        )
        self.assertEqual(
            argv,
            (
                "/usr/bin/python3",
                "-B",
                "-m",
                "mlx_lm.server",
                "--host",
                "127.0.0.1",
                "--port",
                "31418",
                "--model",
                "/models/qwen",
            ),
        )
        control = lifecycle_adapter_argv("mlx-lm-openai-server")
        self.assertEqual(control[0], sys.executable)
        self.assertEqual(control[1:3], ("-B", "-m"))
        self.assertNotIn("shell", control)

    def test_unknown_arguments_and_non_loopback_listener_fail_closed(self) -> None:
        base = {
            "executable": "/opt/bin/llama-server",
            "host": "127.0.0.1",
            "port": 8080,
            "model": "/models/model.gguf",
        }
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            build_target_argv("llama-cpp-openai-server", {**base, "plugin": "evil.module"})
        with self.assertRaisesRegex(ContractError, "literal loopback"):
            build_target_argv("llama-cpp-openai-server", {**base, "host": "localhost"})
        with self.assertRaisesRegex(ContractError, "match engine_family"):
            registration_for("llama-cpp-openai-server", "turnvector")

    def test_concurrent_target_manifests_resolve_only_exact_templates(self) -> None:
        bindings = {
            "target_checkout": "/checkout",
            "model_root": "/models/qwen",
            "python_executable": "/usr/bin/python3",
            "port": 31418,
        }
        expected_commands = {
            "turnvector-openai-v1.json": "turnvector-openai-server",
            "ax-engine-openai-v1.json": "ax-engine-openai-server",
            "mlx-lm-openai-v1.json": "mlx-lm-openai-server",
            "llama-cpp-openai-v1.json": "llama-cpp-openai-server",
        }
        for filename, command_id in expected_commands.items():
            with self.subTest(target=filename):
                target = json.loads((ROOT / "targets" / filename).read_text())
                registration, argv = resolve_target_adapter(target, bindings)
                self.assertEqual(registration.command_id, command_id)
                self.assertIn("127.0.0.1", argv)
                self.assertIn("31418", argv)
                self.assertNotIn("{port}", argv)

    def test_process_and_listener_evidence_binds_owned_group(self) -> None:
        records = (
            ProcessRecord(100, 1, 100, 10, "/bin/a", "a" * 64, "start-a"),
            ProcessRecord(101, 100, 100, 20, "/bin/b", "b" * 64, "start-b"),
        )
        validate_process_ownership(
            records,
            process_group_leader=100,
            expected_process_ids=[100, 101],
            listener=ListenerRecord("127.0.0.1", 31418, 101),
        )
        self.assertTrue(identities_stable(records, records))
        with self.assertRaisesRegex(ContractError, "listener owner"):
            validate_process_ownership(
                records,
                process_group_leader=100,
                expected_process_ids=[100, 101],
                listener=ListenerRecord("127.0.0.1", 31418, 999),
            )
        changed = (
            records[0],
            ProcessRecord(101, 100, 100, 20, "/bin/b", "b" * 64, "reused-pid"),
        )
        self.assertFalse(identities_stable(records, changed))


if __name__ == "__main__":
    unittest.main()
