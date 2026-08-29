from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.determinism import (
    DeterminismIdentity,
    DeterminismObservation,
    OutputObservation,
    RouteObservation,
    StateObservation,
    assess_determinism,
)


D = "a" * 64


class CrossEngineDeterminismTests(unittest.TestCase):
    def identities(self):
        return (
            DeterminismIdentity(
                "request-0", "corpus-a", 0, "request-body-v1", "config-v1", "model-v1"
            ),
            DeterminismIdentity(
                "request-1", "corpus-a", 1, "request-body-v1", "config-v1", "model-v1"
            ),
        )

    def output(self, *, text=b"same", finish="stop", usage=None, terminal=None,
               derived=D, native=D):
        return OutputObservation(
            visible_output=text,
            finish_reason=finish,
            authoritative_usage=usage or {
                "prompt_tokens": 4,
                "completion_tokens": 1,
                "total_tokens": 5,
            },
            terminal_sequence=terminal or ("finish:stop", "usage", "done"),
            derived_output_token_hash=derived,
            native_output_token_hash=native,
            native_output_token_ids_validated=native is not None,
        )

    def observations(self, *, outputs=None, routes=None, states=None):
        identities = self.identities()
        outputs = outputs or (self.output(), self.output())
        routes = routes or (
            RouteObservation("corroborated", ("backend:mlx", "execution:direct")),
            RouteObservation("corroborated", ("backend:mlx", "execution:direct")),
        )
        states = states or (
            StateObservation("declared", ("cold", "resident"), D),
            StateObservation("declared", ("cold", "resident"), D),
        )
        return tuple(
            DeterminismObservation(identity, output, route, state)
            for identity, output, route, state in zip(identities, outputs, routes, states)
        )

    def assess(self, observations):
        return assess_determinism(observations, planned_identities=self.identities())

    def test_equal_repetitions_report_independent_determinism_claim(self):
        result = self.assess(self.observations())
        self.assertTrue(result.output_deterministic)
        self.assertTrue(result.route_deterministic)
        self.assertTrue(result.state_deterministic)
        self.assertEqual(result.not_observable, ())
        projection = result.as_dict()
        self.assertEqual(projection["semantic_claim"], "determinism")
        self.assertEqual(projection["output_deterministic"], True)
        self.assertEqual(projection["route_deterministic"], True)
        self.assertEqual(projection["state_deterministic"], True)

    def test_each_dimension_can_fail_without_contaminating_the_others(self):
        changed_output = self.assess(self.observations(outputs=(
            self.output(), self.output(text=b"different")
        )))
        self.assertFalse(changed_output.output_deterministic)
        self.assertTrue(changed_output.route_deterministic)
        self.assertTrue(changed_output.state_deterministic)

        changed_route = self.assess(self.observations(routes=(
            RouteObservation("corroborated", ("execution:direct",)),
            RouteObservation("corroborated", ("execution:mtp",)),
        )))
        self.assertTrue(changed_route.output_deterministic)
        self.assertFalse(changed_route.route_deterministic)
        self.assertTrue(changed_route.state_deterministic)

        changed_state = self.assess(self.observations(states=(
            StateObservation("declared", ("cold", "resident"), D),
            StateObservation("declared", ("cold", "evicted"), D),
        )))
        self.assertTrue(changed_state.output_deterministic)
        self.assertTrue(changed_state.route_deterministic)
        self.assertFalse(changed_state.state_deterministic)

    def test_output_identity_includes_finish_usage_and_terminal_sequence(self):
        cases = (
            (self.output(), self.output(finish="length")),
            (self.output(), self.output(usage={
                "prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6,
            })),
            (self.output(), self.output(terminal=("finish:stop", "done"))),
        )
        for outputs in cases:
            with self.subTest(outputs=outputs):
                result = self.assess(self.observations(outputs=outputs))
                self.assertFalse(result.output_deterministic)
                self.assertTrue(result.route_deterministic)
                self.assertTrue(result.state_deterministic)

    def test_partial_or_level_drift_is_unobservable_only_for_that_dimension(self):
        missing_route = self.assess(self.observations(routes=(
            RouteObservation("declared", ("execution:direct",)), None,
        )))
        self.assertTrue(missing_route.output_deterministic)
        self.assertIsNone(missing_route.route_deterministic)
        self.assertTrue(missing_route.state_deterministic)
        self.assertIn("route", missing_route.not_observable)

        level_drift = self.assess(self.observations(states=(
            StateObservation("declared", ("cold", "resident"), D),
            StateObservation("corroborated", ("cold", "resident"), D),
        )))
        self.assertTrue(level_drift.output_deterministic)
        self.assertTrue(level_drift.route_deterministic)
        self.assertIsNone(level_drift.state_deterministic)
        self.assertIn("state", level_drift.not_observable)

        surface_drift = self.assess(self.observations(states=(
            StateObservation("declared", ("cold", "resident"), D),
            StateObservation("declared", ("cold", "resident"), None),
        )))
        self.assertTrue(surface_drift.output_deterministic)
        self.assertTrue(surface_drift.route_deterministic)
        self.assertIsNone(surface_drift.state_deterministic)

    def test_absent_optional_token_identity_does_not_forge_or_poison_output(self):
        outputs = (
            self.output(derived=None, native=None),
            self.output(derived=None, native=None),
        )
        result = self.assess(self.observations(outputs=outputs))
        self.assertTrue(result.output_deterministic)
        self.assertTrue(result.route_deterministic)
        self.assertTrue(result.state_deterministic)
        self.assertEqual(result.not_observable, ())

    def test_missing_duplicate_extra_and_reordered_rows_fail_closed(self):
        rows = self.observations()
        invalid = (
            rows[:1],
            (rows[0], rows[0]),
            rows + (rows[0],),
            tuple(reversed(rows)),
        )
        for observations in invalid:
            with self.subTest(observations=observations):
                with self.assertRaisesRegex(
                    ContractError, "missing, duplicated, extra, reordered"
                ):
                    self.assess(observations)

    def test_request_corpus_repetition_and_execution_identities_are_frozen(self):
        rows = list(self.observations())
        rows[1] = DeterminismObservation(
            DeterminismIdentity(
                "request-1", "corpus-a", 2,
                "request-body-v1", "config-v1", "model-v1",
            ),
            rows[1].output,
            rows[1].route,
            rows[1].state,
        )
        with self.assertRaises(ContractError):
            self.assess(rows)

        bad_plan = list(self.identities())
        bad_plan[1] = DeterminismIdentity(
            "request-1", "corpus-a", 1,
            "different-request", "config-v1", "model-v1",
        )
        observations = tuple(
            DeterminismObservation(identity, self.output()) for identity in bad_plan
        )
        with self.assertRaisesRegex(ContractError, "request/config/model identity"):
            assess_determinism(observations, planned_identities=bad_plan)

    def test_native_token_hash_requires_validated_exposed_ids(self):
        with self.assertRaisesRegex(ContractError, "validated exposed native token IDs"):
            OutputObservation(
                b"same", "stop", None, ("done",),
                native_output_token_hash=D,
            )
        with self.assertRaisesRegex(ContractError, "validated exposed native token IDs"):
            OutputObservation(
                b"same", "stop", None, ("done",),
                native_output_token_ids_validated=True,
            )


if __name__ == "__main__":
    unittest.main()
