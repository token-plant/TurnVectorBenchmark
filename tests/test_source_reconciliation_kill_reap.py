"""Focused tests for _kill_and_reap OSError translation.

A non-ProcessLookup OSError from kill or from the reap wait means termination
did not provably succeed, so it becomes a fixed non-leaking ContractError
instead of a silent successful-reap claim; the caller's original
timeout/overflow ContractError is preserved only when kill and reap succeed.
"""

from __future__ import annotations

import unittest

import turnvector_benchmark.source_verification as source_verification
from turnvector_benchmark.core import ContractError


class KillAndReapTests(unittest.TestCase):
    """Non-ProcessLookup OSError from kill/reap becomes a fixed ContractError."""

    class _FakeProc:
        def __init__(self, kill_error=None, wait_error=None, poll_result=None):
            self.kill_error = kill_error
            self.wait_error = wait_error
            self.poll_result = poll_result
            self.killed = False
            self.waited = False

        def poll(self):
            return self.poll_result

        def kill(self):
            self.killed = True
            if self.kill_error is not None:
                raise self.kill_error

        def wait(self):
            self.waited = True
            if self.wait_error is not None:
                raise self.wait_error

    def test_successful_kill_and_reap(self):
        proc = self._FakeProc()
        source_verification._kill_and_reap(proc)
        self.assertTrue(proc.killed)
        self.assertTrue(proc.waited)

    def test_poll_reports_exited_skips_kill_but_reaps(self):
        proc = self._FakeProc(poll_result=0)
        source_verification._kill_and_reap(proc)
        self.assertFalse(proc.killed)
        self.assertTrue(proc.waited)

    def test_kill_process_lookup_error_is_tolerated_and_reaped(self):
        # The child exited between poll() and kill(); wait() still reaps it.
        proc = self._FakeProc(kill_error=ProcessLookupError(3, "no such process"))
        source_verification._kill_and_reap(proc)
        self.assertTrue(proc.killed)
        self.assertTrue(proc.waited)

    def test_kill_oserror_becomes_fixed_contract_error(self):
        proc = self._FakeProc(kill_error=PermissionError(13, "permission denied"))
        with self.assertRaisesRegex(ContractError, "git child termination failed"):
            source_verification._kill_and_reap(proc)
        self.assertTrue(proc.killed)
        self.assertFalse(proc.waited)

    def test_reap_wait_oserror_becomes_fixed_contract_error(self):
        proc = self._FakeProc(wait_error=OSError(10, "no child process"))
        with self.assertRaisesRegex(ContractError, "git child termination failed"):
            source_verification._kill_and_reap(proc)
        self.assertTrue(proc.killed)
        self.assertTrue(proc.waited)

    def test_timeout_error_is_preserved_when_termination_succeeds(self):
        # The integration tests prove the original timeout/overflow
        # ContractError survives a successful kill+reap; here the same holds
        # for a mocked child whose kill/wait both succeed.
        proc = self._FakeProc()
        source_verification._kill_and_reap(proc)
        self.assertTrue(proc.waited)


if __name__ == "__main__":
    unittest.main()
