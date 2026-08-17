from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .controller import LaneController
from .core import ContractError, load_suite
from .expectation import (
    bind_suite_lane,
    expectation_summary,
    load_expectation,
)
from .evidence import write_checksums, write_json
from .performance import load_performance_contract
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

    inspect = subparsers.add_parser(
        "inspect", help="derive full benchmark readiness from contracts and self-tests"
    )
    inspect.add_argument("--expectation", type=Path, required=True)
    inspect.add_argument("--target-repo", type=Path)

    inspect_performance = subparsers.add_parser(
        "inspect-performance",
        help="inspect the performance publication contract and expanded case plan",
    )
    inspect_performance.add_argument("--contract", type=Path, required=True)

    validate_performance = subparsers.add_parser(
        "validate-performance",
        help="independently validate one performance evidence artifact",
    )
    validate_performance.add_argument("--contract", type=Path, required=True)
    validate_performance.add_argument("--evidence", type=Path, required=True)
    validate_performance.add_argument("--output", type=Path)

    def add_controller_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--expectation", type=Path, required=True)
        command.add_argument("--subject-manifest", type=Path)
        command.add_argument("--certification-record", type=Path)
        command.add_argument("--external-fixtures", type=Path)
        command.add_argument("--target-repo", type=Path)
        command.add_argument("--profile", default="qualification")
        command.add_argument("--output", type=Path, required=True)

    run_lane = subparsers.add_parser(
        "run-lane", help="execute one required lane through SubjectAdapter v1"
    )
    add_controller_arguments(run_lane)
    run_lane.add_argument("--lane", required=True)

    run_all = subparsers.add_parser(
        "run-all", help="execute all required lanes without cross-lane short circuiting"
    )
    add_controller_arguments(run_all)

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
        if args.command == "inspect":
            print(
                json.dumps(
                    LaneController.inspect(
                        expectation_path=args.expectation,
                        target_repo=args.target_repo,
                    ),
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "inspect-performance":
            contract = load_performance_contract(args.contract)
            print(json.dumps(contract.inspect(), sort_keys=True))
            return 0
        if args.command == "validate-performance":
            contract = load_performance_contract(args.contract)
            report = contract.validate_artifact(args.evidence)
            if args.output is not None:
                output = args.output.resolve()
                if output.exists():
                    raise FileExistsError(
                        f"performance validation output already exists: {output}"
                    )
                output.mkdir(parents=True)
                write_json(output / "report.json", report)
                write_checksums(output)
                rendered = {
                    "status": report["status"],
                    "promotion_status": report["promotion_status"],
                    "publication_candidate": report["publication_candidate"],
                    "report": str(output / "report.json"),
                }
            else:
                rendered = report
            print(json.dumps(rendered, sort_keys=True))
            return {
                "publishable": 0,
                "not_publishable": 3,
                "unsupported": 4,
            }[report["status"]]
        if args.command in {"run-lane", "run-all"}:
            controller = LaneController(
                expectation_path=args.expectation,
                subject_manifest_path=args.subject_manifest,
                certification_record_path=args.certification_record,
                external_fixture_manifest_path=args.external_fixtures,
                output_dir=args.output,
                target_repo=args.target_repo,
                profile=args.profile,
            )
            result = (
                controller.run_lane(args.lane)
                if args.command == "run-lane"
                else controller.run_all()
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "full_implementation_status": result.report[
                            "full_implementation_status"
                        ],
                        "artifact_dir": str(result.artifact_dir),
                        "report": str(result.artifact_dir / "report.json"),
                    },
                    sort_keys=True,
                )
            )
            return result.exit_code
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
