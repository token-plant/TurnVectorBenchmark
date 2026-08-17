from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkflowContractTests(unittest.TestCase):
    def test_real_qualification_jobs_require_a_passed_claim(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "apple-qualification.yml").read_text(
            encoding="utf-8"
        )
        assertion = (
            "jq -e '.full_implementation_status == \"passed\" and .claimable == true'"
        )
        self.assertEqual(workflow.count(assertion), 2)


if __name__ == "__main__":
    unittest.main()
