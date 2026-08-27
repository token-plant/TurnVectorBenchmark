from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.fixture_provenance import (
    BENCHMARK_FIXTURE,
    EXECUTION_PROVENANCE_VALUES,
    PRODUCTION_SUBJECT,
    CaseStartMonitor,
    ExecutionProvenance,
    validate_execution_provenance,
)

FIXTURE_ID = "owner-lifecycle-device-executor-v1"


class ExecutionProvenanceContractTests(unittest.TestCase):
    def test_only_two_admitted_values_in_frozen_order(self) -> None:
        self.assertEqual(
            EXECUTION_PROVENANCE_VALUES, ("production_subject", "benchmark_fixture")
        )

    def test_production_subject_requires_no_fixture_id(self) -> None:
        provenance = ExecutionProvenance(PRODUCTION_SUBJECT, None)
        self.assertEqual(provenance.value, "production_subject")
        self.assertIsNone(provenance.fixture_id)
        self.assertEqual(
            provenance.as_dict(),
            {"execution_provenance": "production_subject", "fixture_id": None},
        )

    def test_benchmark_fixture_requires_a_fixture_id(self) -> None:
        provenance = ExecutionProvenance(BENCHMARK_FIXTURE, FIXTURE_ID)
        self.assertEqual(provenance.value, "benchmark_fixture")
        self.assertEqual(provenance.fixture_id, FIXTURE_ID)

    def test_missing_fixture_id_fails_closed(self) -> None:
        for fixture_id in (None, "", 0, ["owner-lifecycle"]):
            with self.assertRaisesRegex(ContractError, "requires a non-empty fixture_id"):
                ExecutionProvenance(BENCHMARK_FIXTURE, fixture_id)

    def test_unknown_provenance_value_fails_closed(self) -> None:
        for value in (None, "", "production", "benchmark", "mystery_fixture", 7):
            with self.assertRaisesRegex(ContractError, "unknown execution provenance"):
                ExecutionProvenance(value, None)

    def test_fixture_id_forbidden_for_production_subject(self) -> None:
        with self.assertRaisesRegex(ContractError, "forbids fixture_id"):
            ExecutionProvenance(PRODUCTION_SUBJECT, FIXTURE_ID)
        with self.assertRaisesRegex(ContractError, "forbids fixture_id"):
            ExecutionProvenance(PRODUCTION_SUBJECT, "")

    def test_fixture_id_must_match_identifier_grammar(self) -> None:
        for fixture_id in ("owner lifecycle", "OwnerLifecycle", "../escape"):
            with self.assertRaisesRegex(ContractError, "identifier grammar"):
                ExecutionProvenance(BENCHMARK_FIXTURE, fixture_id)
        with self.assertRaisesRegex(ContractError, "must not exceed 128"):
            ExecutionProvenance(BENCHMARK_FIXTURE, "x" * 129)

    def test_validate_execution_provenance_is_the_single_strict_parser(self) -> None:
        parsed = validate_execution_provenance(BENCHMARK_FIXTURE, FIXTURE_ID)
        self.assertIsInstance(parsed, ExecutionProvenance)
        self.assertEqual(parsed.fixture_id, FIXTURE_ID)
        with self.assertRaises(ContractError):
            validate_execution_provenance(BENCHMARK_FIXTURE, None)
        with self.assertRaises(ContractError):
            validate_execution_provenance("driver_or_context", None)
        with self.assertRaises(ContractError):
            validate_execution_provenance(PRODUCTION_SUBJECT, FIXTURE_ID)

    def test_constructor_and_parser_agree_on_every_rejection(self) -> None:
        cases = [
            (None, None),
            (BENCHMARK_FIXTURE, None),
            (PRODUCTION_SUBJECT, FIXTURE_ID),
            ("unknown", None),
            (BENCHMARK_FIXTURE, "bad fixture id"),
        ]
        for value, fixture_id in cases:
            with self.assertRaises(ContractError):
                ExecutionProvenance(value, fixture_id)
            with self.assertRaises(ContractError):
                validate_execution_provenance(value, fixture_id)


class CaseStartMonitorTests(unittest.TestCase):
    def test_starts_empty_and_marks_idempotently(self) -> None:
        monitor = CaseStartMonitor()
        self.assertFalse(monitor.first_case_started)
        self.assertEqual(monitor.started_lanes, ())
        monitor.mark_case_started("core-event-replay")
        monitor.mark_case_started("core-event-replay")
        monitor.mark_case_started("scheduler-policy")
        self.assertTrue(monitor.first_case_started)
        self.assertTrue(monitor.has_started("core-event-replay"))
        self.assertFalse(monitor.has_started("protocol-and-owner-lifecycle"))
        self.assertEqual(monitor.started_lanes, ("core-event-replay", "scheduler-policy"))


if __name__ == "__main__":
    unittest.main()
