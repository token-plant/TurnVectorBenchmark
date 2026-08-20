from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from unittest.mock import patch

from turnvector_benchmark.cli import main
from turnvector_benchmark.core import ContractError, canonical_json
from turnvector_benchmark.evidence import sha256_file, write_json, write_jsonl
from turnvector_benchmark.gateway_validation import load_gateway_validation_contract


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "profiles" / "gateway-validation-v1.json"
SCHEMAS = (
    ROOT / "schemas" / "gateway-validation-contract-v1.schema.json",
    ROOT / "schemas" / "gateway-validation-evidence-v1.schema.json",
    ROOT / "schemas" / "gateway-validation-run-manifest-v1.schema.json",
    ROOT / "schemas" / "gateway-validation-raw-v1.schema.json",
)


class GatewayValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_gateway_validation_contract(CONTRACT)

    @staticmethod
    def _events(items: List[Tuple[int, str, str]]) -> List[Mapping[str, Any]]:
        return [
            {
                "sequence": sequence,
                "request_role": role,
                "kind": kind,
                "at_ns": at_ns,
            }
            for sequence, (at_ns, role, kind) in enumerate(sorted(items))
        ]

    def _lifecycle_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        event_suffixes = {
            "fast-fit": [
                (80, "a", "terminal_observed"),
                (90, "a", "http_last_byte_committed"),
                (100, "a", "request_state_release_accepted"),
                (110, "a", "response_closed"),
            ],
            "slow-fit": [
                (80, "a", "terminal_observed"),
                (90, "a", "request_state_release_accepted"),
                (100, "b", "peer_request_progress"),
                (140, "a", "http_last_byte_committed"),
                (150, "a", "response_closed"),
            ],
            "stalled-fit": [
                (80, "a", "terminal_observed"),
                (90, "a", "request_state_release_accepted"),
                (100, "a", "backpressure_entered"),
                (110, "b", "peer_request_progress"),
                (130, "a", "deadline_expired"),
                (140, "a", "response_closed"),
            ],
            "stalled-overflow": [
                (60, "a", "backpressure_entered"),
                (80, "a", "deadline_expired"),
                (90, "a", "cancel_ordered"),
                (100, "a", "terminal_observed"),
                (110, "a", "request_state_release_accepted"),
                (120, "a", "response_closed"),
            ],
            "disconnect-mid-stream": [
                (60, "a", "client_disconnected"),
                (70, "a", "cancel_ordered"),
                (80, "a", "terminal_observed"),
                (90, "a", "request_state_release_accepted"),
                (100, "a", "response_closed"),
            ],
        }
        base = [
            (0, "a", "exchange_reserved"),
            (10, "a", "request_accepted"),
            (20, "a", "backend_ownership_acquired"),
            (30, "a", "output_production_started"),
        ]
        for case in self.contract.lifecycle_cases:
            reader_rate = {
                "fast": 4096,
                "slow": 1024,
                "stalled": 0,
                "disconnect": 0,
            }[case.reader_mode]
            rows.append(
                {
                    "case_id": case.case_id,
                    "events": self._events(base + event_suffixes[case.case_id]),
                    "counters": {
                        counter_id: 0 for counter_id in self.contract.counter_ids
                    },
                    "queue": {
                        "capacity_bytes": 1024,
                        "initial_occupancy_bytes": 0,
                        "producer_bytes_per_second": 2048,
                        "consumer_bytes_per_second": reader_rate,
                        "peak_occupancy_bytes": 1024,
                    },
                }
            )
        return rows

    def _transport_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        plan = self.contract.transport_case_plan()
        for repetition in range(self.contract.transport_protocol.measured_repetitions):
            ordered = plan if repetition % 2 == 0 else tuple(reversed(plan))
            for case in ordered:
                production = case.parameters["probe_path"] == "production_data_plane"
                rows.append(
                    {
                        "case_id": case.case_id,
                        "repetition": repetition,
                        "stages": {
                            "socket_ns": 10 + repetition % 5,
                            "connect_accept_ns": 20,
                            "peer_credential_ns": 30,
                            "hello_ns": 40 if production else 0,
                            "descriptor_validation_ns": 50 if production else 0,
                        },
                        "request_variable_ns": 25,
                        "cpu_ns": 75,
                        "context_switches": 2,
                        "frame_payload_bytes": [10, 20] if production else [],
                        "fd_peak": 2,
                        "connection_count": 1,
                        "error_count": 0,
                        "first_response_ns": 200 if production else 0,
                    }
                )
        return rows

    @staticmethod
    def _descriptor(artifact_id: str, root: Path, path: Path) -> Mapping[str, Any]:
        return {
            "id": artifact_id,
            "path": str(path.relative_to(root)),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "custody": "benchmark",
        }

    def _write_fixture(
        self,
        root: Path,
        *,
        subject_kind: str = "implementation",
        lifecycle_mutator: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        transport_mutator: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> Tuple[Path, Dict[str, Any]]:
        lifecycle_rows = self._lifecycle_rows()
        transport_rows = self._transport_rows()
        if lifecycle_mutator is not None:
            lifecycle_mutator(lifecycle_rows)
        if transport_mutator is not None:
            transport_mutator(transport_rows)
        lifecycle_path = root / "raw" / "lifecycle.jsonl"
        transport_path = root / "raw" / "transport.jsonl"
        host_path = root / "raw" / "host.jsonl"
        write_jsonl(lifecycle_path, lifecycle_rows)
        write_jsonl(transport_path, transport_rows)
        write_jsonl(
            host_path,
            [
                {
                    "at_ns": 0,
                    "load_average_1m": 0.1,
                    "top_process_cpu_percent": 0.0,
                    "thermal_state": "nominal",
                }
            ],
        )
        limits = {
            "http_write_no_progress_ns": 1000,
            "http_write_total_ns": 1000,
            "cancellation_completion_ns": 1000,
        }
        limits_digest = hashlib.sha256(canonical_json(limits).encode("ascii")).hexdigest()
        run_manifest_path = root / "run-manifest.json"
        write_json(
            run_manifest_path,
            {
                "schema_version": "turnvector.benchmark.gateway-validation-run-manifest.v1",
                "contract": {
                    "id": self.contract.contract_id,
                    "sha256": self.contract.sha256,
                },
                "source_contract_sha256": self.contract.source_contract.sha256,
                "session_id": "gateway-fixture-session",
                "effective_limits": limits,
                "transport_protocol": self.contract.transport_protocol.as_dict(),
                "lifecycle_case_ids": [
                    case.case_id for case in self.contract.lifecycle_cases
                ],
                "transport_case_plan": [
                    case.as_dict() for case in self.contract.transport_case_plan()
                ],
            },
        )
        value: Dict[str, Any] = {
            "schema_version": "turnvector.benchmark.gateway-validation-evidence.v1",
            "contract": {
                "id": self.contract.contract_id,
                "sha256": self.contract.sha256,
            },
            "session": {
                "id": "gateway-fixture-session",
                "subject_kind": subject_kind,
                "started_at": "2026-08-20T00:00:00Z",
                "finished_at": "2026-08-20T00:01:00Z",
                "turnvector_head_before": "1" * 40,
                "turnvector_head_after": "1" * 40,
                "turnvector_status_before": [],
                "turnvector_status_after": [],
                "benchmark_head_before": "2" * 40,
                "benchmark_head_after": "2" * 40,
                "benchmark_status_before": [],
                "benchmark_status_after": [],
                "source_contract_sha256": self.contract.source_contract.sha256,
                "gateway_build_sha256": "3" * 64,
                "daemon_build_sha256": "4" * 64,
                "compatibility_profile_sha256": "5" * 64,
                "route_manifest_sha256": "6" * 64,
                "tokenizer_template_sha256": "7" * 64,
                "model_revision_sha256": "8" * 64,
                "data_plane_descriptor_sha256": "9" * 64,
                "effective_limits_sha256": limits_digest,
                "clock_mode": "single_monotonic",
                "clock_calibration_sha256": None,
            },
            "environment": {
                "hardware_id": "fixture-mac",
                "memory_bytes": 1024,
                "os_build": "fixture-os",
                "power_source": "external",
                "thermal_state_start": "nominal",
                "thermal_state_end": "nominal",
                "host_admission_passed": True,
            },
            "effective_limits": limits,
            "artifacts": [
                self._descriptor("run_manifest", root, run_manifest_path),
                self._descriptor("lifecycle_trace", root, lifecycle_path),
                self._descriptor("transport_trials", root, transport_path),
                self._descriptor("host_samples", root, host_path),
            ],
        }
        evidence_path = root / "evidence.json"
        write_json(evidence_path, value)
        return evidence_path, value

    @staticmethod
    def _rewrite_manifest(path: Path, value: Mapping[str, Any]) -> None:
        write_json(path, value)

    @staticmethod
    def _refresh_artifact(
        root: Path, value: Dict[str, Any], artifact_id: str
    ) -> Path:
        descriptor = next(
            item for item in value["artifacts"] if item["id"] == artifact_id
        )
        path = root / descriptor["path"]
        descriptor["size"] = path.stat().st_size
        descriptor["sha256"] = sha256_file(path)
        return path

    def test_contract_expands_fixed_gateway_plan(self) -> None:
        report = self.contract.inspect()
        self.assertEqual(report["lifecycle_case_count"], 5)
        self.assertEqual(report["transport_case_count"], 32)
        self.assertEqual(report["transport_trial_count"], 3200)
        self.assertFalse(report["ownership_change_authorized"])
        for schema in SCHEMAS:
            self.assertIsInstance(json.loads(schema.read_text(encoding="utf-8")), dict)

    def test_gateway_v1_cannot_silently_drop_safety_fields(self) -> None:
        for field in ("event_kinds", "counter_ids"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "contract.json"
                value = json.loads(CONTRACT.read_text(encoding="utf-8"))
                value["lifecycle"][field].pop()
                write_json(path, value)
                with self.assertRaisesRegex(ContractError, f"{field} must match"):
                    load_gateway_validation_contract(path)

    def test_source_contract_mismatch_fails_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / self.contract.source_contract.path
            source.parent.mkdir(parents=True)
            source.write_text("wrong", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "source contract hash"):
                self.contract.inspect(root)
            with patch(
                "turnvector_benchmark.gateway_validation.contract.sha256_file",
                return_value=self.contract.source_contract.sha256,
            ):
                self.assertEqual(
                    self.contract.inspect(root)["source_contract_status"], "matched"
                )

    def test_complete_implementation_evidence_is_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._write_fixture(Path(directory))
            report = self.contract.validate_artifact(path)
            self.assertEqual(report["status"], "publishable")
            self.assertEqual(report["lifecycle"]["status"], "passed")
            self.assertEqual(report["transport"]["status"], "measured_baseline")
            self.assertEqual(report["transport"]["trial_count"], 3200)
            self.assertTrue(report["claims"]["backend_response_lifetimes_decoupled"])
            self.assertFalse(report["claims"]["pooling_qualified"])
            first = report["transport"]["cases"][0]
            self.assertEqual(first["setup_ns"]["p99"], 64)
            self.assertEqual(first["wire_bytes"]["p99"], 0)
            self.assertEqual(
                first["predicted_perfect_reuse_upper_bounds"][0][
                    "setup_p99_savings_ns"
                ],
                {"numerator": 32, "denominator": 1},
            )
            stalled = report["lifecycle"]["cases"][2]["metrics"]
            self.assertEqual(
                stalled["predicted_fill_ns"],
                {"numerator": 500000000, "denominator": 1},
            )
            self.assertEqual(stalled["observed_fill_ns"], 70)

    def test_fixture_can_never_make_a_product_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._write_fixture(Path(directory), subject_kind="fixture")
            report = self.contract.validate_artifact(path)
            self.assertEqual(report["status"], "not_claimable_fixture")
            self.assertFalse(report["claims"]["backend_response_lifetimes_decoupled"])
            self.assertFalse(report["claims"]["per_request_uds_baseline_measured"])

    def test_missing_peer_progress_is_negative_evidence(self) -> None:
        def remove_peer(rows: List[Dict[str, Any]]) -> None:
            events = rows[1]["events"]
            rows[1]["events"] = [
                {**event, "sequence": sequence}
                for sequence, event in enumerate(
                    event for event in events if event["kind"] != "peer_request_progress"
                )
            ]

        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._write_fixture(
                Path(directory), lifecycle_mutator=remove_peer
            )
            report = self.contract.validate_artifact(path)
            self.assertEqual(report["status"], "not_publishable")
            self.assertIn(
                "slow-fit:peer_progress_missing_from_response_tail", report["reasons"]
            )

    def test_primary_events_must_belong_to_request_a(self) -> None:
        def move_terminal_to_peer(rows: List[Dict[str, Any]]) -> None:
            event = next(
                event for event in rows[1]["events"] if event["kind"] == "terminal_observed"
            )
            event["request_role"] = "b"

        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._write_fixture(
                Path(directory), lifecycle_mutator=move_terminal_to_peer
            )
            report = self.contract.validate_artifact(path)
            self.assertIn("slow-fit:event_count:terminal_observed", report["reasons"])

    def test_ownership_transfer_is_negative_evidence(self) -> None:
        def record_transfer(rows: List[Dict[str, Any]]) -> None:
            rows[-1]["counters"]["ownership_transfers"] = 1

        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._write_fixture(
                Path(directory), lifecycle_mutator=record_transfer
            )
            report = self.contract.validate_artifact(path)
            self.assertIn(
                "disconnect-mid-stream:counter_nonzero:ownership_transfers",
                report["reasons"],
            )

    def test_calibrated_clock_requires_a_later_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, value = self._write_fixture(Path(directory))
            value["session"]["clock_mode"] = "calibrated"
            value["session"]["clock_calibration_sha256"] = "a" * 64
            self._rewrite_manifest(path, value)
            with self.assertRaisesRegex(ContractError, "one monotonic clock domain"):
                self.contract.validate_artifact(path)

    def test_transport_connection_drift_is_negative_evidence(self) -> None:
        def change_connection(rows: List[Dict[str, Any]]) -> None:
            rows[0]["connection_count"] = 2

        with tempfile.TemporaryDirectory() as directory:
            path, _ = self._write_fixture(
                Path(directory), transport_mutator=change_connection
            )
            report = self.contract.validate_artifact(path)
            self.assertEqual(report["status"], "not_publishable")
            self.assertIn("gateway-uds.0001:connection_count_not_one", report["reasons"])

    def test_transport_order_and_run_manifest_are_independently_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, value = self._write_fixture(root)
            transport = root / value["artifacts"][2]["path"]
            rows = [json.loads(line) for line in transport.read_text(encoding="utf-8").splitlines()]
            rows[0], rows[1] = rows[1], rows[0]
            write_jsonl(transport, rows)
            self._refresh_artifact(root, value, "transport_trials")
            self._rewrite_manifest(path, value)
            with self.assertRaisesRegex(ContractError, "balanced CasePlan order"):
                self.contract.validate_artifact(path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, value = self._write_fixture(root)
            run_manifest = root / value["artifacts"][0]["path"]
            manifest = json.loads(run_manifest.read_text(encoding="utf-8"))
            manifest["transport_protocol"]["measured_repetitions"] = 99
            write_json(run_manifest, manifest)
            self._refresh_artifact(root, value, "run_manifest")
            self._rewrite_manifest(path, value)
            with self.assertRaisesRegex(ContractError, "transport protocol differs"):
                self.contract.validate_artifact(path)

    def test_missing_raw_trial_and_artifact_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, value = self._write_fixture(Path(directory))
            transport = Path(directory) / value["artifacts"][2]["path"]
            rows = transport.read_text(encoding="utf-8").splitlines()
            transport.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
            value["artifacts"][2]["size"] = transport.stat().st_size
            value["artifacts"][2]["sha256"] = sha256_file(transport)
            self._rewrite_manifest(path, value)
            with self.assertRaisesRegex(ContractError, "exactly 3200 rows"):
                self.contract.validate_artifact(path)

            value["artifacts"][2]["sha256"] = "0" * 64
            self._rewrite_manifest(path, value)
            with self.assertRaisesRegex(ContractError, "hash mismatch"):
                self.contract.validate_artifact(path)

    def test_dirty_repository_identity_is_not_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, value = self._write_fixture(Path(directory))
            value["session"]["turnvector_status_before"] = ["M src/lib.rs"]
            value["session"]["turnvector_status_after"] = ["M src/lib.rs"]
            self._rewrite_manifest(path, value)
            report = self.contract.validate_artifact(path)
            self.assertEqual(report["status"], "not_publishable")
            self.assertIn("turnvector_checkout_dirty", report["reasons"])

    def test_cli_writes_authoritative_report_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self._write_fixture(root / "evidence")
            output = root / "report"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "validate-gateway-validation",
                        "--contract",
                        str(CONTRACT),
                        "--evidence",
                        str(path),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "publishable")
            self.assertTrue((output / "report.json").is_file())
            self.assertTrue((output / "SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
