from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from turnvector_benchmark.controller import (
    LaneController,
    resolve_claimable,
    resolve_full_implementation_status,
)
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.lane_contract import resolve_gate_threshold
from turnvector_benchmark.owner_lifecycle_fixture import OWNER_LIFECYCLE_FIXTURE_ID


ROOT = Path(__file__).resolve().parent.parent
EXPECTATION = ROOT / "expectations" / "turnvector-implementation-v1.json"
SUBJECT = ROOT / "subjects" / "reference-fixture-v1.json"
CERTIFICATION = ROOT / "certification" / "reference-fixture-v1.json"


class FixtureTaintClaimabilityTests(unittest.TestCase):
    def output_path(self, name: str = "artifact") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / name

    def controller(self, output: Path) -> LaneController:
        return LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=SUBJECT,
            certification_record_path=CERTIFICATION,
            external_fixture_manifest_path=None,
            output_dir=output,
            target_repo=None,
        )

    def test_unknown_or_inconsistent_taint_state_fails_closed(self) -> None:
        base = dict(
            fixture_subject=False,
            all_required_selected=True,
            aggregate_status="passed",
            evidence_valid=True,
            source_matches=True,
        )
        # An unknown taint string fails closed instead of being treated as clean.
        with self.assertRaisesRegex(ContractError, "unknown run_fixture_taint state"):
            resolve_full_implementation_status(run_fixture_taint="mystery", **base)
        with self.assertRaisesRegex(ContractError, "unknown run_fixture_taint state"):
            resolve_claimable(run_fixture_taint="mystery", **base)
        # Inconsistent bindings fail closed: clean with IDs, tainted without
        # IDs, and tainted with an unknown fixture ID.
        with self.assertRaisesRegex(ContractError, "must bind no fixture IDs"):
            resolve_full_implementation_status(
                run_fixture_taint="clean",
                fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID],
                **base,
            )
        with self.assertRaisesRegex(ContractError, "must bind at least one"):
            resolve_full_implementation_status(
                run_fixture_taint="fixture_tainted",
                fixture_ids=[],
                **base,
            )
        with self.assertRaisesRegex(ContractError, "must be known benchmark fixtures"):
            resolve_full_implementation_status(
                run_fixture_taint="fixture_tainted",
                fixture_ids=["no-such-fixture"],
                **base,
            )

    def test_clean_and_fixture_id_invariant(self) -> None:
        # A clean run binds clean + empty IDs in both manifest and report.
        clean_output = self.output_path("clean")
        clean_result = self.controller(clean_output).run_lane("core-event-replay")
        self.assertEqual(clean_result.report["run_fixture_taint"], "clean")
        self.assertEqual(clean_result.report["fixture_ids"], [])
        clean_manifest = json.loads(
            (clean_output / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(clean_manifest["run_fixture_taint"], "clean")
        self.assertEqual(clean_manifest["fixture_ids"], [])

        # A tainted run binds fixture_tainted + the sorted known fixture IDs.
        tainted_output = self.output_path("tainted")
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-worker-supervision": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            tainted_result = self.controller(tainted_output).run_lane(
                "protocol-and-worker-supervision"
            )
        self.assertEqual(tainted_result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(
            tainted_result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID]
        )
        tainted_manifest = json.loads(
            (tainted_output / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(tainted_manifest["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(
            tainted_manifest["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID]
        )

        # The controller-level invariant rejects inconsistent bindings.
        controller = self.controller(self.output_path("unit"))
        controller.run_fixture_taint = "fixture_tainted"
        controller.fixture_ids = ()
        with self.assertRaisesRegex(ContractError, "must bind at least one"):
            controller._assert_fixture_taint_invariant()
        controller.run_fixture_taint = "clean"
        controller.fixture_ids = (OWNER_LIFECYCLE_FIXTURE_ID,)
        with self.assertRaisesRegex(ContractError, "must bind no fixture IDs"):
            controller._assert_fixture_taint_invariant()

    def test_fixture_ids_input_type_is_strict(self) -> None:
        base = dict(
            fixture_subject=False,
            all_required_selected=True,
            aggregate_status="passed",
            evidence_valid=True,
            source_matches=True,
        )
        # None, a bare string, and a dict are invalid fixture_ids containers
        # and normalize to ContractError (never a bare TypeError).
        for invalid in (None, OWNER_LIFECYCLE_FIXTURE_ID, {"a": 1}):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ContractError, "must be a list or tuple"):
                    resolve_full_implementation_status(
                        run_fixture_taint="fixture_tainted",
                        fixture_ids=invalid,
                        **base,
                    )
                with self.assertRaisesRegex(ContractError, "must be a list or tuple"):
                    resolve_claimable(
                        run_fixture_taint="fixture_tainted",
                        fixture_ids=invalid,
                        **base,
                    )
        # Non-string entries and duplicate known IDs fail closed.
        with self.assertRaisesRegex(ContractError, "must be a known benchmark fixture ID string"):
            resolve_full_implementation_status(
                run_fixture_taint="fixture_tainted",
                fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID, 7],
                **base,
            )
        with self.assertRaisesRegex(ContractError, "must be a known benchmark fixture ID string"):
            resolve_full_implementation_status(
                run_fixture_taint="fixture_tainted",
                fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID, {"nested": True}],
                **base,
            )
        with self.assertRaisesRegex(ContractError, "must not repeat"):
            resolve_full_implementation_status(
                run_fixture_taint="fixture_tainted",
                fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID, OWNER_LIFECYCLE_FIXTURE_ID],
                **base,
            )

    def test_fixture_ids_require_canonical_sorted_order(self) -> None:
        second = "second-benchmark-fixture-v1"
        with patch.dict(
            "turnvector_benchmark.owner_lifecycle_fixture.FIXTURE_DESCRIPTORS",
            {second: {"fixture_id": second, "execution_provenance": "benchmark_fixture"}},
        ):
            base = dict(
                fixture_subject=False,
                all_required_selected=True,
                aggregate_status="passed",
                evidence_valid=True,
                source_matches=True,
            )
            # Canonical sorted order is required even when every ID is known.
            with self.assertRaisesRegex(ContractError, "canonical sorted order"):
                resolve_full_implementation_status(
                    run_fixture_taint="fixture_tainted",
                    fixture_ids=[second, OWNER_LIFECYCLE_FIXTURE_ID],
                    **base,
                )
            with self.assertRaisesRegex(ContractError, "canonical sorted order"):
                resolve_claimable(
                    run_fixture_taint="fixture_tainted",
                    fixture_ids=[second, OWNER_LIFECYCLE_FIXTURE_ID],
                    **base,
                )
            # The same known IDs in canonical sorted order bind cleanly.
            self.assertEqual(
                resolve_full_implementation_status(
                    run_fixture_taint="fixture_tainted",
                    fixture_ids=[OWNER_LIFECYCLE_FIXTURE_ID, second],
                    **base,
                ),
                "not_claimable_fixture",
            )

    def test_run_fixture_taint_must_be_a_real_string(self) -> None:
        base = dict(
            fixture_subject=False,
            all_required_selected=True,
            aggregate_status="passed",
            evidence_valid=True,
            source_matches=True,
        )

        class _ExplodingEquality:
            """Hostile value whose custom __eq__ must never be reached."""

            def __eq__(self, other: Any) -> bool:
                del other
                raise AssertionError("hostile __eq__ must never run")

            def __repr__(self) -> str:
                return "<exploding-equality>"

        # None, an integer, and a hostile object whose __eq__ raises are all
        # non-strings: they normalize to ContractError before any membership
        # comparison, so a custom equality can never raise out of the strict
        # taint-state validation.
        for invalid in (None, 7, _ExplodingEquality()):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ContractError, "unknown run_fixture_taint state"
                ):
                    resolve_full_implementation_status(
                        run_fixture_taint=invalid, **base
                    )
                with self.assertRaisesRegex(
                    ContractError, "unknown run_fixture_taint state"
                ):
                    resolve_claimable(run_fixture_taint=invalid, **base)

    def test_fixture_selected_plus_early_threshold_failure_remains_tainted(self) -> None:
        output = self.output_path()
        controller = self.controller(output)

        def fail_selected_thresholds(
            lane_id: str,
            gate: Any,
            record: Any,
            *,
            observed_at: Any = None,
        ) -> Any:
            if gate.gate_id in {"decision-latency", "decision-throughput"}:
                raise ContractError(f"cannot freeze {gate.gate_id}")
            return resolve_gate_threshold(lane_id, gate, record, observed_at=observed_at)

        with patch(
            "turnvector_benchmark.controller.resolve_gate_threshold",
            side_effect=fail_selected_thresholds,
        ):
            with patch.dict(
                "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
                {"scheduler-performance": OWNER_LIFECYCLE_FIXTURE_ID},
            ):
                result = controller.run_lane("scheduler-performance")
        # The threshold snapshot failure blocks execution, but the selected
        # fixture still tainted the run before any lane work.
        self.assertEqual(result.status, "contract_failed")
        self.assertEqual(result.report["lanes"][0]["executed_case_count"], 0)
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], result.report["run_fixture_taint"])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])

    def test_fixture_selected_plus_early_adapter_failure_remains_tainted(self) -> None:
        output = self.output_path()
        controller = LaneController(
            expectation_path=EXPECTATION,
            subject_manifest_path=None,
            certification_record_path=CERTIFICATION,
            external_fixture_manifest_path=None,
            output_dir=output,
            target_repo=None,
        )
        with patch.dict(
            "turnvector_benchmark.controller.FIXTURE_SELECTION_SEAM",
            {"protocol-and-worker-supervision": OWNER_LIFECYCLE_FIXTURE_ID},
        ):
            result = controller.run_lane("protocol-and-worker-supervision")
        # The adapter failure blocks execution, but the selected fixture still
        # tainted the run before any lane work.
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.report["lanes"][0]["executed_case_count"], 0)
        self.assertEqual(result.report["run_fixture_taint"], "fixture_tainted")
        self.assertEqual(result.report["fixture_ids"], [OWNER_LIFECYCLE_FIXTURE_ID])
        self.assertEqual(
            result.report["full_implementation_status"], "not_claimable_fixture"
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_fixture_taint"], result.report["run_fixture_taint"])
        self.assertEqual(manifest["fixture_ids"], result.report["fixture_ids"])


if __name__ == "__main__":
    unittest.main()
