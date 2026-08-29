from __future__ import annotations

import unittest

from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.routes import (
    NativeRouteRecord,
    RouteCapture,
    build_route_evidence,
    correlate_route_records,
    normalize_native_route,
)


KNOWN = {
    "backend": "mlx",
    "execution": "mtp",
    "cache": "memory_prefix",
    "model_state": "resident",
    "fallback": "none",
}


class CrossEngineRouteTests(unittest.TestCase):
    def capture(self, request_id: str, record=KNOWN, **overrides) -> RouteCapture:
        arguments = {
            "process_identity_verified": True,
            "config_identity_verified": True,
            "route_specific_corroboration": True,
        }
        arguments.update(overrides)
        return RouteCapture(NativeRouteRecord(request_id, record), **arguments)

    def test_native_record_is_retained_and_normalization_is_separate(self):
        native = dict(KNOWN, engine_detail={"blocks": 7}, execution="future-mode")
        record = NativeRouteRecord("request-1", native)
        native["engine_detail"]["blocks"] = 99
        evidence = build_route_evidence("request-1", RouteCapture(record))
        self.assertEqual(evidence.native.record["engine_detail"], {"blocks": 7})
        self.assertEqual(evidence.normalized.execution, "other")
        self.assertEqual(evidence.observation_level, "declared")
        self.assertFalse(evidence.supports_positive_claim("mtp"))

    def test_registered_value_map_and_verified_evidence_levels(self):
        record = {"backend": "metal", "execution": "draft", "cache": "ram",
                  "model_state": "loaded", "fallback": "ok"}
        maps = {
            "backend": {"metal": "mlx"}, "execution": {"draft": "mtp"},
            "cache": {"ram": "memory_prefix"}, "model_state": {"loaded": "resident"},
            "fallback": {"ok": "none"},
        }
        evidence = build_route_evidence("r", self.capture("r", record), value_maps=maps)
        self.assertEqual(evidence.observation_level, "corroborated")
        self.assertTrue(evidence.supports_positive_claim("mtp"))
        forced = build_route_evidence(
            "r", self.capture("r", record, benchmark_forced=True), value_maps=maps
        )
        self.assertEqual(forced.observation_level, "benchmark_forced")

    def test_unknown_or_absent_dimension_fails_closed(self):
        unknown = build_route_evidence(
            "r", self.capture("r", {key: value for key, value in KNOWN.items()
                                     if key != "fallback"})
        )
        self.assertEqual(unknown.normalized.fallback, "other")
        self.assertFalse(unknown.supports_positive_claim("mtp"))
        self.assertEqual(normalize_native_route({}).backend, "other")
        self.assertEqual(normalize_native_route({"execution": {"future": True}}).execution,
                         "other")

    def test_request_correlation_keeps_missing_records_client_only(self):
        rows = correlate_route_records(["a", "b"], [self.capture("b")])
        self.assertEqual([row.request_id for row in rows], ["a", "b"])
        self.assertEqual(rows[0].observation_level, "client_only")
        self.assertIsNone(rows[0].native)
        with self.assertRaises(ContractError):
            correlate_route_records(["a"], [self.capture("a"), self.capture("a")])
        with self.assertRaises(ContractError):
            correlate_route_records(["a"], [self.capture("outside")])


if __name__ == "__main__":
    unittest.main()
