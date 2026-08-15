from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .core import ContractError, load_suite
from .expectation import (
    bind_suite_lane,
    expectation_summary,
    load_expectation,
)
from .runner import BenchmarkRunner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="turnvector-benchmark",
        description="Run the independent TurnVector benchmark suite.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate suite and scenario contracts")
    validate.add_argument("--suite", type=Path, required=True)

    expectation = subparsers.add_parser(
        "expectation", help="inspect the complete implementation expectation contract"
    )
    expectation.add_argument("--manifest", type=Path, required=True)
    expectation.add_argument("--target-repo", type=Path)

    run = subparsers.add_parser("run", help="run a suite against a JSONL driver")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--expectation", type=Path, required=True)
    run.add_argument("--lane", required=True)
    run.add_argument("--driver-command", required=True)
    run.add_argument("--driver-cwd", type=Path, default=Path.cwd())
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--target-repo", type=Path)
    run.add_argument("--response-timeout-seconds", type=float, default=5.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "expectation":
            expectation = load_expectation(args.manifest.resolve())
            print(
                json.dumps(
                    expectation_summary(expectation, args.target_repo), sort_keys=True
                )
            )
            return 0
        suite = load_suite(args.suite.resolve())
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "suite_id": suite.suite_id,
                        "scenario_count": len(suite.scenarios),
                        "expanded_turn_count": sum(
                            scenario.total_turns * scenario.repetitions
                            for scenario in suite.scenarios
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 0
        expectation = load_expectation(args.expectation.resolve())
        lane = bind_suite_lane(expectation, args.lane, suite)
        if args.response_timeout_seconds <= 0:
            raise ContractError("response timeout must be greater than zero")
        runner = BenchmarkRunner(
            suite=suite,
            expectation=expectation,
            lane=lane,
            driver_command=args.driver_command,
            driver_cwd=args.driver_cwd,
            output_dir=args.output,
            target_repo=args.target_repo,
            response_timeout_seconds=args.response_timeout_seconds,
        )
        result = runner.run()
        print(
            json.dumps(
                {
                    "status": result.status,
                    "artifact_dir": str(result.artifact_dir),
                    "report": str(result.artifact_dir / "report.json"),
                },
                sort_keys=True,
            )
        )
        return result.exit_code
    except (ContractError, FileExistsError) as error:
        print(f"contract error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
