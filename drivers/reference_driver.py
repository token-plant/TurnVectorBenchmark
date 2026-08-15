#!/usr/bin/env python3
"""Small independent implementation of the benchmark driver protocol."""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from typing import Any, Dict, List, Optional, Set


PROTOCOL = "turnvector.benchmark.driver.v1"


def fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


class ReferenceScheduler:
    def __init__(self, models: List[Dict[str, Any]]) -> None:
        self.weights = {model["model_id"]: model["weight"] for model in models}
        self.ledgers = {model_id: Fraction(0) for model_id in self.weights}
        self.runnable: Set[str] = set()
        self.baseline = Fraction(0)
        self.pending: Optional[Dict[str, Any]] = None

    def ledger_map(self, model_ids: Optional[Set[str]] = None) -> Dict[str, str]:
        selected = set(self.ledgers) if model_ids is None else model_ids
        return {
            model_id: fraction_text(self.ledgers[model_id])
            for model_id in sorted(selected)
        }

    @staticmethod
    def eligible(candidate: Dict[str, Any]) -> bool:
        return all(
            candidate[field]
            for field in (
                "capability_authorized",
                "resource_safe",
                "timing_feasible",
                "output_reserved",
            )
        )

    def schedule(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if self.pending is not None:
            raise ValueError("schedule received before pending receipt")
        candidates = message["candidates"]
        eligible = [candidate for candidate in candidates if self.eligible(candidate)]
        current = {candidate["model_id"] for candidate in eligible}
        continuing = current & self.runnable
        if continuing:
            alignment = min(self.ledgers[model_id] for model_id in continuing)
        else:
            alignment = self.baseline
        for model_id in current - self.runnable:
            self.ledgers[model_id] = alignment
        self.runnable = current
        if current:
            self.baseline = min(self.ledgers[model_id] for model_id in current)

        urgent = []
        for candidate in eligible:
            obligation = candidate["timing_obligation_us"]
            if obligation is None:
                continue
            latest = (
                obligation
                - candidate["engine_service_bound_us"]
                - candidate["runtime_overhead_bound_us"]
            )
            if message["now_us"] >= latest:
                urgent.append((latest, candidate))
        if urgent:
            selected = min(
                urgent,
                key=lambda item: (
                    item[0],
                    self.ledgers[item[1]["model_id"]],
                    item[1]["candidate_id"],
                ),
            )[1]
        elif eligible:
            selected = min(
                eligible,
                key=lambda candidate: (
                    self.ledgers[candidate["model_id"]],
                    candidate["model_id"],
                    candidate["candidate_id"],
                ),
            )
        else:
            selected = None
        self.pending = selected
        return {
            "kind": "plan",
            "sequence": message["sequence"],
            "candidate_id": selected["candidate_id"] if selected else None,
            "runnable_ledgers_us": self.ledger_map(current),
        }

    def receipt(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if self.pending is None:
            raise ValueError("receipt received without a pending plan")
        if (
            message["candidate_id"] != self.pending["candidate_id"]
            or message["model_id"] != self.pending["model_id"]
        ):
            raise ValueError("receipt does not match pending plan")
        model_id = message["model_id"]
        self.ledgers[model_id] += Fraction(
            message["actual_engine_service_us"], self.weights[model_id]
        )
        self.pending = None
        if self.runnable:
            self.baseline = min(self.ledgers[item] for item in self.runnable)
        return {
            "kind": "receipt_accepted",
            "sequence": message["sequence"],
            "model_ledgers_us": self.ledger_map(),
        }


def emit(value: Dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def main() -> int:
    scheduler: Optional[ReferenceScheduler] = None
    for line in sys.stdin:
        try:
            message = json.loads(line)
            kind = message.get("kind")
            if kind == "hello":
                if message.get("protocol_version") != PROTOCOL:
                    raise ValueError("unsupported protocol")
                emit(
                    {
                        "kind": "hello_ack",
                        "protocol_version": PROTOCOL,
                        "driver_name": "reference-scheduler",
                        "driver_version": "0.1.0",
                    }
                )
            elif kind == "initialize":
                scheduler = ReferenceScheduler(message["models"])
                emit(
                    {
                        "kind": "initialized",
                        "scenario_id": message["scenario_id"],
                        "model_ledgers_us": scheduler.ledger_map(),
                    }
                )
            elif kind == "schedule":
                if scheduler is None:
                    raise ValueError("driver is not initialized")
                emit(scheduler.schedule(message))
            elif kind == "receipt":
                if scheduler is None:
                    raise ValueError("driver is not initialized")
                emit(scheduler.receipt(message))
            elif kind == "shutdown":
                emit({"kind": "shutdown_ack"})
                return 0
            else:
                raise ValueError(f"unsupported message kind {kind!r}")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(f"reference driver contract error: {error}", file=sys.stderr, flush=True)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
