from __future__ import annotations

import unittest
from itertools import product

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.fixture_provenance import BENCHMARK_FIXTURE
from turnvector_benchmark.owner_lifecycle_fixture import (
    CLIENT_PROTOCOL_RELATIONS,
    DAEMON_OUTCOMES,
    FAILURE_INJECTIONS,
    FIXTURE_DESCRIPTORS,
    FIXTURE_SELECTION_SEAM,
    MAX_CLIENT_FRAME_BYTES,
    OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR,
    OWNER_LIFECYCLE_FIXTURE_ID,
    OWNER_LIFECYCLE_FIXTURE_SCHEMA,
    DeviceExecutorFixture,
    describe,
    expand_owner_lifecycle_plans,
    fixture_descriptor,
    injectable_outcomes,
    known_fixture_ids,
    protocol_acceptance,
    validate_client_protocol_relation,
    validate_daemon_outcome,
)

ACCEPTED_RELATIONS = ("exact", "compatible")
REJECTED_RELATIONS = ("incompatible", "unknown_capability")


class OwnerLifecycleFixtureContractTests(unittest.TestCase):
    def test_describe_never_claims_a_real_backend_or_mlx_worker(self) -> None:
        value = describe()
        self.assertEqual(value["fixture"], OWNER_LIFECYCLE_FIXTURE_ID)
        self.assertTrue(value["same_process"])
        self.assertEqual(value["daemon_processes"], 1)
        self.assertFalse(value["real_backend_interface"])
        self.assertFalse(value["separate_mlx_worker"])
        self.assertFalse(value["claimable"])
        self.assertEqual(value["plan_count"], 24)
        self.assertEqual(
            tuple(value["daemon_outcomes"]),
            DAEMON_OUTCOMES,
        )
        self.assertEqual(
            tuple(value["client_protocol_relations"]),
            CLIENT_PROTOCOL_RELATIONS,
        )

    def test_frozen_matrix_domains(self) -> None:
        self.assertEqual(
            DAEMON_OUTCOMES,
            (
                "normal",
                "failure_before_backend_initialization",
                "failure_during_turn",
                "safe_point_timeout",
                "malformed_client_frame",
                "duplicate_client_command",
            ),
        )
        self.assertEqual(
            CLIENT_PROTOCOL_RELATIONS,
            ("exact", "compatible", "incompatible", "unknown_capability"),
        )

    def test_six_frozen_failure_injections(self) -> None:
        self.assertEqual(
            set(FAILURE_INJECTIONS),
            set(DAEMON_OUTCOMES),
        )
        self.assertEqual(len(injectable_outcomes()), 6)
        self.assertEqual(
            injectable_outcomes(),
            tuple(f"inject-{outcome}" for outcome in DAEMON_OUTCOMES),
        )

    def test_twenty_four_plans_cover_six_outcomes_times_four_relations(self) -> None:
        plans = expand_owner_lifecycle_plans()
        self.assertEqual(len(plans), 24)
        self.assertEqual(len({plan.case_id for plan in plans}), 24)
        self.assertEqual([plan.ordinal for plan in plans], list(range(1, 25)))
        covered = {
            (plan.daemon_outcome, plan.client_protocol_relation) for plan in plans
        }
        self.assertEqual(
            covered,
            set(product(DAEMON_OUTCOMES, CLIENT_PROTOCOL_RELATIONS)),
        )
        self.assertEqual(
            tuple((plan.daemon_outcome, plan.client_protocol_relation) for plan in plans),
            tuple(product(DAEMON_OUTCOMES, CLIENT_PROTOCOL_RELATIONS)),
        )
        for plan in plans:
            self.assertEqual(
                plan.parameters(),
                {
                    "daemon_outcome": plan.daemon_outcome,
                    "client_protocol_relation": plan.client_protocol_relation,
                },
            )
        self.assertEqual(
            [plan.case_id for plan in expand_owner_lifecycle_plans()],
            [plan.case_id for plan in plans],
        )

    def test_descriptor_and_seam_are_active_in_pr4(self) -> None:
        descriptor = OWNER_LIFECYCLE_FIXTURE_DESCRIPTOR
        self.assertEqual(descriptor["fixture_id"], OWNER_LIFECYCLE_FIXTURE_ID)
        self.assertEqual(
            descriptor["schema_version"], OWNER_LIFECYCLE_FIXTURE_SCHEMA
        )
        self.assertEqual(descriptor["execution_provenance"], BENCHMARK_FIXTURE)
        self.assertTrue(descriptor["same_process"])
        self.assertFalse(descriptor["real_backend_interface"])
        self.assertFalse(descriptor["separate_mlx_worker"])
        self.assertFalse(descriptor["claimable"])
        self.assertEqual(
            known_fixture_ids(), (OWNER_LIFECYCLE_FIXTURE_ID,)
        )
        self.assertIs(fixture_descriptor(OWNER_LIFECYCLE_FIXTURE_ID), descriptor)
        self.assertIsNone(fixture_descriptor("no-such-fixture"))
        # PR 4 activates the owner-lifecycle lane through the fixture
        # selection seam under the absorbing fixture-taint interlock.
        self.assertEqual(
            FIXTURE_SELECTION_SEAM,
            {"protocol-and-owner-lifecycle": OWNER_LIFECYCLE_FIXTURE_ID},
        )
        self.assertEqual(set(FIXTURE_DESCRIPTORS), {OWNER_LIFECYCLE_FIXTURE_ID})

    def test_protocol_acceptance_domains(self) -> None:
        for relation in ACCEPTED_RELATIONS:
            self.assertTrue(protocol_acceptance(relation))
        for relation in REJECTED_RELATIONS:
            self.assertFalse(protocol_acceptance(relation))

    def test_invalid_outcome_or_relation_fails_closed(self) -> None:
        for outcome in (None, "", "crash_before_start", "mystery", 3):
            with self.assertRaisesRegex(ContractError, "unknown daemon_outcome"):
                validate_daemon_outcome(outcome)
        for relation in (None, "", "exactness", "compatible_v2", 3):
            with self.assertRaisesRegex(ContractError, "unknown client_protocol_relation"):
                validate_client_protocol_relation(relation)
        for outcome, relation in (
            ("mystery", "exact"),
            ("normal", "mystery"),
            (None, None),
        ):
            with self.assertRaises(ContractError):
                DeviceExecutorFixture(outcome, relation)

    def test_fixture_is_deterministic(self) -> None:
        signatures = [
            DeviceExecutorFixture("normal", "exact").trajectory_signature()
            for _ in range(2)
        ]
        self.assertEqual(signatures[0], signatures[1])
        self.assertEqual(
            DeviceExecutorFixture("failure_during_turn", "incompatible").trajectory_signature(),
            DeviceExecutorFixture("failure_during_turn", "incompatible").trajectory_signature(),
        )

    def test_injection_mismatch_fails_closed(self) -> None:
        fixture = DeviceExecutorFixture("normal", "exact")
        with self.assertRaisesRegex(ContractError, "injection mismatch"):
            fixture.inject_daemon_outcome("failure_during_turn")
        injection = fixture.inject_daemon_outcome("normal")
        self.assertEqual(injection["injection"], "inject-normal")
        self.assertTrue(injection["frozen"])
        self.assertTrue(injection["deterministic"])


class DeviceExecutorFixtureTrajectoryTests(unittest.TestCase):
    """Six outcomes x four protocol relations: every plan fails closed."""

    def _run(self, outcome: str, relation: str) -> dict:
        fixture = DeviceExecutorFixture(outcome, relation)
        launch = fixture.launch_fixture_daemon()
        inspect = fixture.inspect_daemon_fail_closed()
        self.assertEqual(launch["operation"], "launch-fixture-daemon")
        self.assertEqual(launch["fixture_id"], OWNER_LIFECYCLE_FIXTURE_ID)
        self.assertTrue(launch["same_process"])
        self.assertFalse(launch["real_backend_interface"])
        self.assertFalse(launch["separate_mlx_worker"])
        self.assertEqual(inspect["operation"], "inspect-daemon-fail-closed")
        return inspect

    def test_all_twenty_four_plans_fail_closed(self) -> None:
        for outcome, relation in product(DAEMON_OUTCOMES, CLIENT_PROTOCOL_RELATIONS):
            with self.subTest(outcome=outcome, relation=relation):
                inspect = self._run(outcome, relation)
                self.assertTrue(inspect["fail_closed"], inspect["failures"])
                self.assertEqual(inspect["failures"], [])
                self.assertFalse(inspect["real_backend_interface"])
                self.assertFalse(inspect["separate_mlx_worker"])
                metrics = inspect["metrics"]
                self.assertLessEqual(
                    metrics["simultaneous_device_owner_count"], 1
                )
                self.assertEqual(
                    metrics["backend_calls_before_initialization_count"], 0
                )
                self.assertEqual(
                    metrics["successful_receipt_after_daemon_loss_count"], 0
                )
                self.assertLessEqual(
                    metrics["client_transport_max_frame_bytes"],
                    MAX_CLIENT_FRAME_BYTES,
                )
                evidence = inspect["evidence"]
                for artifact in (
                    "bootstrap_trace",
                    "client_transport_trace",
                    "process_trace",
                    "turn_receipts",
                ):
                    self.assertIn(artifact, evidence)

    def test_rejected_relations_never_start_a_turn(self) -> None:
        for outcome in DAEMON_OUTCOMES:
            for relation in REJECTED_RELATIONS:
                with self.subTest(outcome=outcome, relation=relation):
                    inspect = self._run(outcome, relation)
                    self.assertEqual(inspect["daemon_state"], "protocol_rejected")
                    self.assertEqual(inspect["evidence"]["turn_receipts"], [])
                    self.assertEqual(inspect["metrics"]["simultaneous_device_owner_count"], 0)

    def test_normal_outcome_completes_one_committed_turn(self) -> None:
        inspect = self._run("normal", "exact")
        self.assertEqual(inspect["daemon_state"], "turn_completed")
        receipts = inspect["evidence"]["turn_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["committed"])
        self.assertFalse(receipts[0]["after_daemon_loss"])
        self.assertEqual(
            inspect["metrics"]["client_transport_latency_samples_us"],
            [100, 110, 120],
        )

    def test_failure_before_backend_initialization_makes_no_backend_call(self) -> None:
        inspect = self._run("failure_before_backend_initialization", "exact")
        self.assertEqual(inspect["daemon_state"], "daemon_lost")
        self.assertEqual(inspect["evidence"]["turn_receipts"], [])
        self.assertEqual(
            inspect["metrics"]["backend_calls_before_initialization_count"], 0
        )
        bootstrap = inspect["evidence"]["bootstrap_trace"]
        self.assertTrue(
            any(
                item["event"] == "device_executor_initialization_failed"
                for item in bootstrap
            )
        )
        self.assertTrue(
            all(item["event"] != "backend_call" for item in bootstrap)
        )

    def test_failure_during_turn_is_indeterminate_without_fabricated_receipt(self) -> None:
        inspect = self._run("failure_during_turn", "compatible")
        self.assertEqual(inspect["daemon_state"], "daemon_lost")
        receipts = inspect["evidence"]["turn_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["started"])
        self.assertFalse(receipts[0]["committed"])
        self.assertTrue(receipts[0]["after_daemon_loss"])
        self.assertEqual(receipts[0]["outcome"], "indeterminate")

    def test_safe_point_timeout_never_commits_a_receipt(self) -> None:
        inspect = self._run("safe_point_timeout", "exact")
        self.assertEqual(inspect["daemon_state"], "timed_out_failed_closed")
        receipts = inspect["evidence"]["turn_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertFalse(receipts[0]["committed"])
        self.assertFalse(receipts[0]["after_daemon_loss"])

    def test_malformed_client_frame_is_rejected_before_execution(self) -> None:
        inspect = self._run("malformed_client_frame", "exact")
        self.assertEqual(inspect["daemon_state"], "failed_closed")
        self.assertEqual(inspect["evidence"]["turn_receipts"], [])
        frames = [
            item
            for item in inspect["evidence"]["client_transport_trace"]
            if item.get("event") == "client_frame"
        ]
        self.assertEqual(len(frames), 1)
        self.assertFalse(frames[0]["accepted"])
        self.assertTrue(frames[0]["malformed"])
        self.assertGreater(frames[0]["frame_bytes"], MAX_CLIENT_FRAME_BYTES)

    def test_duplicate_client_command_is_at_most_once(self) -> None:
        inspect = self._run("duplicate_client_command", "exact")
        self.assertEqual(inspect["daemon_state"], "turn_completed")
        receipts = inspect["evidence"]["turn_receipts"]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["committed"])
        commands = [
            item
            for item in inspect["evidence"]["client_transport_trace"]
            if item.get("event") == "client_command"
        ]
        self.assertEqual(len(commands), 2)
        self.assertFalse(commands[1]["accepted"])
        self.assertTrue(commands[1]["duplicate"])

    def test_normal_plan_satisfies_successor_gate_metrics(self) -> None:
        inspect = self._run("normal", "compatible")
        metrics = inspect["metrics"]
        self.assertEqual(metrics["simultaneous_device_owner_count"], 1)
        self.assertEqual(metrics["client_transport_max_frame_bytes"], 512)
        self.assertTrue(
            all(sample > 0 for sample in metrics["client_transport_latency_samples_us"])
        )
        self.assertLessEqual(
            max(metrics["client_transport_latency_samples_us"]), 1000
        )


if __name__ == "__main__":
    unittest.main()
