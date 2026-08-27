from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkflowContractTests(unittest.TestCase):
    def test_real_qualification_jobs_require_nonclaimable_d0_evidence(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "apple-qualification.yml").read_text(
            encoding="utf-8"
        )
        # D0 deliberately activates the nonclaimable owner-lifecycle fixture
        # because the real Adapter remains blocked, so the unreachable
        # passed/claimable gate must not reappear. Both real jobs (full
        # qualification and instruments nightly) must fail closed on the exact
        # negative-evidence gate instead: full_implementation_status is
        # not_claimable_fixture, claimable is false, run_fixture_taint is
        # fixture_tainted, and fixture_ids is exactly the owner-lifecycle
        # fixture ID.
        negative_evidence_gate = (
            'jq -e \'.full_implementation_status == "not_claimable_fixture" '
            'and .claimable == false and .run_fixture_taint == "fixture_tainted" '
            'and .fixture_ids == ["owner-lifecycle-device-executor-v1"]\''
        )
        self.assertEqual(workflow.count(negative_evidence_gate), 2)
        self.assertEqual(
            workflow.count("Require nonclaimable D0 owner-lifecycle fixture evidence"),
            2,
        )
        self.assertNotIn(
            '.full_implementation_status == "passed" and .claimable == true',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
