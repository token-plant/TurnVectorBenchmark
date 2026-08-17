from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from typing import List

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.lane_contract import SubjectAdapter
from turnvector_benchmark.subject import SubjectSession


ROOT = Path(__file__).resolve().parent.parent


class SubjectSessionTests(unittest.TestCase):
    def test_request_write_is_bounded_by_adapter_timeout(self) -> None:
        adapter = SubjectAdapter(
            adapter_id="blocked-stdin",
            category="core",
            command=(sys.executable, "-c", "import time; time.sleep(60)"),
            cwd=ROOT,
            lanes=("core-event-replay",),
            timeout_seconds=0.05,
        )
        session = SubjectSession(adapter)
        errors: List[BaseException] = []
        session.__enter__()
        try:
            thread = threading.Thread(
                target=lambda: self._capture_request_error(session, errors), daemon=True
            )
            thread.start()
            thread.join(timeout=0.5)
            completed_within_bound = not thread.is_alive()
            if thread.is_alive():
                assert session.process is not None
                session.process.terminate()
                session.process.wait(timeout=2)
                thread.join(timeout=2)
        finally:
            session.__exit__(None, None, None)

        self.assertTrue(completed_within_bound, "stdin write exceeded the adapter timeout")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ContractError)
        self.assertIn("timed out", str(errors[0]))

    @staticmethod
    def _capture_request_error(
        session: SubjectSession, errors: List[BaseException]
    ) -> None:
        try:
            session.request({"kind": "case_step", "payload": "x" * (4 * 1024 * 1024)})
        except BaseException as error:
            errors.append(error)


if __name__ == "__main__":
    unittest.main()
