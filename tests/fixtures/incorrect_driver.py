#!/usr/bin/env python3
"""Protocol-valid driver with intentionally wrong selection and accounting."""

import json
import sys


models = []
for line in sys.stdin:
    message = json.loads(line)
    kind = message["kind"]
    if kind == "hello":
        response = {
            "kind": "hello_ack",
            "protocol_version": "turnvector.benchmark.driver.v1",
            "driver_name": "incorrect-test-driver",
            "driver_version": "1",
        }
    elif kind == "initialize":
        models = [model["model_id"] for model in message["models"]]
        response = {
            "kind": "initialized",
            "scenario_id": message["scenario_id"],
            "model_ledgers_us": {model_id: "0/1" for model_id in models},
        }
    elif kind == "schedule":
        eligible = [
            candidate
            for candidate in message["candidates"]
            if candidate["capability_authorized"]
            and candidate["resource_safe"]
            and candidate["timing_feasible"]
            and candidate["output_reserved"]
        ]
        response = {
            "kind": "plan",
            "sequence": message["sequence"],
            "candidate_id": eligible[0]["candidate_id"] if eligible else None,
            "runnable_ledgers_us": {
                candidate["model_id"]: "0/1" for candidate in eligible
            },
        }
    elif kind == "receipt":
        response = {
            "kind": "receipt_accepted",
            "sequence": message["sequence"],
            "model_ledgers_us": {model_id: "0/1" for model_id in models},
        }
    elif kind == "shutdown":
        response = {"kind": "shutdown_ack"}
        print(json.dumps(response), flush=True)
        raise SystemExit(0)
    else:
        raise SystemExit(2)
    print(json.dumps(response), flush=True)
