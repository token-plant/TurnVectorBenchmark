"""Process-bound, single-use CompilePermit tests."""

import copy
import os
import pickle
import unittest
from unittest import mock

from turnvector_benchmark.authority import compile_permit
from turnvector_benchmark.authority.compile_permit import PermitPayload
from turnvector_benchmark.authority.compiler_inputs import compute_input_set_sha256
from turnvector_benchmark.authority.errors import (
    CONTRACT_FAILURE_VARIANTS,
    CompilerInternalError,
    CompilerPreconditionViolation,
)

from tests.fixtures.compiler.fixture_utils import build_fixture, digest
from tests.fixtures.compiler.test_permit_issuer import (
    issue_test_compile_permit,
    payload_fields,
)


class CompilePermitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def assert_precondition(self, reason, callable_, *args, **kwargs):
        with self.assertRaises(CompilerPreconditionViolation) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.reason, reason)
        return raised.exception

    def test_single_consume_and_reuse_failure(self):
        permit = issue_test_compile_permit(self.fixture)
        payload = permit.consume()
        self.assertIs(payload, permit.payload)
        self.assert_precondition("permit_reuse", permit.consume)

    def test_copy_deepcopy_pickle_and_subclass_are_prohibited(self):
        factories = [lambda p: copy.copy(p), lambda p: copy.deepcopy(p),
                     lambda p: pickle.dumps(p)]
        for factory in factories:
            with self.subTest(factory=factory):
                with self.assertRaises(CompilerInternalError):
                    factory(issue_test_compile_permit(self.fixture))
        with self.assertRaises(CompilerInternalError):
            class SubPermit(compile_permit.CompilePermit):
                pass

    def test_payload_byte_count_u64_construction_boundary(self):
        base = payload_fields(self.fixture)
        for value in (-1, 1 << 64, True, 1.0, "1"):
            with self.subTest(value=value):
                fields = dict(base)
                fields["catalog_byte_count"] = value
                self.assert_precondition("input_identity_mismatch", PermitPayload, **fields)

    def test_attempt_tmax_identifier_and_digest_domains(self):
        base = payload_fields(self.fixture)
        cases = [
            ("attempt", 0), ("attempt", 9), ("t_max", 7),
            ("custody_domain_id", "Bad/Domain"),
            ("catalog_sha256", "A" * 64), ("input_set_sha256", "0" * 63),
        ]
        for name, value in cases:
            with self.subTest(field=name):
                fields = dict(base)
                fields[name] = value
                self.assert_precondition("input_identity_mismatch", PermitPayload, **fields)

    def test_payload_input_set_digest_binds_all_counts_and_digests(self):
        fields = payload_fields(self.fixture)
        payload = PermitPayload(**fields)
        self.assertEqual(payload.input_set_sha256, compute_input_set_sha256(payload))
        for name, raw in zip(
            ("authority_snapshot", "source_reconciliation", "expectation", "catalog", "traceability"),
            self.fixture.input_buffers,
        ):
            self.assertEqual(getattr(payload, f"{name}_byte_count"), len(raw))
            self.assertEqual(getattr(payload, f"{name}_sha256"), digest(raw))
        changed = dict(fields)
        changed["catalog_byte_count"] += 1
        provisional = PermitPayload(**changed)
        self.assertNotEqual(compute_input_set_sha256(provisional), payload.input_set_sha256)

    def test_f46_and_b2_bind_synthetic_expectation_digest(self):
        for variant in ("f46", "b2"):
            with self.subTest(variant=variant):
                fixture = build_fixture(variant=variant)
                payload = issue_test_compile_permit(fixture).payload
                expected = digest(fixture.expectation_bytes)
                self.assertEqual(payload.expectation_sha256, expected)
                self.assertEqual(fixture.catalog[0]["expectation_sha256"], expected)
                self.assertEqual(fixture.ledger["binds"]["expectation_sha256"], expected)
                self.assertEqual(payload.input_set_sha256, compute_input_set_sha256(payload))

    def test_pid_mismatch_is_sole_internal_exception(self):
        permit = issue_test_compile_permit(self.fixture)
        with mock.patch("turnvector_benchmark.authority.compile_permit.os.getpid",
                        return_value=os.getpid() + 1):
            with self.assertRaises(CompilerInternalError) as raised:
                permit._check_issuing_process()
        self.assertNotIsInstance(raised.exception, CompilerPreconditionViolation)
        self.assertNotIn(type(raised.exception).__name__, CONTRACT_FAILURE_VARIANTS)

    def test_stored_token_identity_checks_are_disjoint(self):
        test_permit = issue_test_compile_permit(self.fixture)
        self.assert_precondition("compiler_identity_mismatch",
                                 test_permit._check_production_entry)
        test_permit._check_test_entry()
        fields = PermitPayload(**payload_fields(self.fixture))
        production = compile_permit._issue_compile_permit(
            fields, compile_permit._PRODUCTION_ENTRY_TOKEN
        )
        production._check_production_entry()
        with self.assertRaises(CompilerInternalError):
            production._check_test_entry()

    def test_wrong_issuance_tokens_raise_exact_internal_exception(self):
        payload = PermitPayload(**payload_fields(self.fixture))
        for factory in (compile_permit._issue_compile_permit,
                        compile_permit._issue_test_compile_permit):
            with self.subTest(factory=factory.__name__):
                with self.assertRaises(CompilerInternalError) as raised:
                    factory(payload, object())
                self.assertIs(type(raised.exception), CompilerInternalError)

    def test_frame_guards_detect_misuse_but_tokens_are_module_readable(self):
        with self.assertRaises(CompilerInternalError):
            compile_permit._production_entry_token()
        if __name__.startswith("tests."):
            token = compile_permit._test_issuer_token()
            self.assertIs(token, compile_permit._TEST_ISSUER_TOKEN)
        else:
            # unittest discovery imports this module as ``test_compile_permit``;
            # that name is intentionally outside the frozen ``tests.`` guard.
            with self.assertRaises(CompilerInternalError):
                compile_permit._test_issuer_token()
        # This is intentionally a same-user convention guard, not secrecy.
        self.assertIsNotNone(compile_permit._PRODUCTION_ENTRY_TOKEN)
        self.assertIsNotNone(compile_permit._TEST_ISSUER_TOKEN)

    def test_issuance_kind_is_bookkeeping_not_input_set_identity(self):
        fields = payload_fields(self.fixture)
        test_payload = PermitPayload(**fields)
        prod_fields = dict(fields, issuance_kind="production")
        production_payload = PermitPayload(**prod_fields)
        self.assertEqual(compute_input_set_sha256(test_payload),
                         compute_input_set_sha256(production_payload))
        self.assertNotIn(b"issuance_kind", compile_permit.__doc__.encode())


if __name__ == "__main__":
    unittest.main()
