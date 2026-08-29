from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .controller import LaneController
from .core import ContractError, load_suite
from .cross_engine.artifacts import (
    ArtifactSpec,
    build_artifact_manifest,
    write_artifact_manifest,
    write_sha256s_from_manifest,
)
from .cross_engine.baseline import promote_baseline
from .cross_engine.campaign import freeze_campaign
from .cross_engine.controller import CrossEngineController
from .cross_engine.comparison import (
    comparison_summary,
    coverage_intersection,
    paired_rows,
)
from .cross_engine.contracts import canonical_json_bytes, strict_json_loads
from .cross_engine.native import (
    NativeInferencePlan,
    parse_native_trials,
    summarize_native_trials,
)
from .cross_engine.metrics import summarize_observations
from .cross_engine.reporting import StatusAxes, cross_engine_exit_code
from .cross_engine.serving import CommonServingExecutor, HuggingFaceWorkload
from .expectation import (
    bind_suite_lane,
    expectation_summary,
    load_expectation,
)
from .evidence import write_checksums, write_json
from .gateway_validation import load_gateway_validation_contract
from .performance import load_performance_contract
from .runner import BenchmarkRunner


_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_ROOT = _REPOSITORY_ROOT / "schemas"
_MAX_CONTRACT_BYTES = 16 * 1024 * 1024


def _schema_pointer(root: Mapping[str, Any], reference: str) -> Any:
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise ContractError("cross-engine schemas may use local JSON pointers only")
    value: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or part not in value:
            raise ContractError(f"schema reference does not resolve: {reference}")
        value = value[part]
    return value


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ContractError(f"schema uses unsupported type {expected!r}")


def _validate_schema(
    value: Any,
    schema: Any,
    root: Mapping[str, Any],
    where: str = "$",
) -> None:
    """Validate the closed Draft 2020-12 subset used by cross-engine v1."""
    if schema is True:
        return
    if schema is False or not isinstance(schema, Mapping):
        raise ContractError(f"{where} is rejected by its schema")
    if "$ref" in schema:
        _validate_schema(value, _schema_pointer(root, schema["$ref"]), root, where)
    if "allOf" in schema:
        for branch in schema["allOf"]:
            _validate_schema(value, branch, root, where)
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate_schema(value, branch, root, where)
                break
            except ContractError:
                pass
        else:
            raise ContractError(f"{where} must match at least one schema branch")
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                _validate_schema(value, branch, root, where)
                matches += 1
            except ContractError:
                pass
        if matches != 1:
            raise ContractError(f"{where} must match exactly one schema branch")
    if "not" in schema:
        try:
            _validate_schema(value, schema["not"], root, where)
        except ContractError:
            pass
        else:
            raise ContractError(f"{where} matches a forbidden schema")
    if "if" in schema:
        try:
            _validate_schema(value, schema["if"], root, where)
        except ContractError:
            branch = schema.get("else")
        else:
            branch = schema.get("then")
        if branch is not None:
            _validate_schema(value, branch, root, where)

    expected = schema.get("type")
    if expected is not None:
        expected_types = (expected,) if isinstance(expected, str) else tuple(expected)
        if not any(_schema_type_matches(value, item) for item in expected_types):
            raise ContractError(f"{where} has the wrong type")
    if "const" in schema and (
        type(value) is not type(schema["const"]) or value != schema["const"]
    ):
        raise ContractError(f"{where} does not equal its required constant")
    if "enum" in schema and not any(
        type(value) is type(item) and value == item for item in schema["enum"]
    ):
        raise ContractError(f"{where} is outside its allowed values")

    if isinstance(value, dict):
        missing = [name for name in schema.get("required", ()) if name not in value]
        if missing:
            raise ContractError(f"{where} is missing required fields: {', '.join(missing)}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            item_where = f"{where}.{name}"
            if name in properties:
                _validate_schema(item, properties[name], root, item_where)
            elif additional is False:
                raise ContractError(f"{item_where} is an unknown field")
            elif isinstance(additional, Mapping):
                _validate_schema(item, additional, root, item_where)
        if len(value) < schema.get("minProperties", 0):
            raise ContractError(f"{where} has too few fields")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ContractError(f"{where} has too many fields")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ContractError(f"{where} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ContractError(f"{where} has too many items")
        if schema.get("uniqueItems"):
            encoded = [canonical_json_bytes(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ContractError(f"{where} contains duplicate items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, root, f"{where}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ContractError(f"{where} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractError(f"{where} is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ContractError(f"{where} does not match its required pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ContractError(f"{where} must be finite")
        if "minimum" in schema and number < schema["minimum"]:
            raise ContractError(f"{where} is below its minimum")
        if "exclusiveMinimum" in schema and number <= schema["exclusiveMinimum"]:
            raise ContractError(f"{where} is not above its exclusive minimum")
        if "maximum" in schema and number > schema["maximum"]:
            raise ContractError(f"{where} is above its maximum")


def _read_regular_bytes(path: Path, where: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as error:
        raise ContractError(f"cannot inspect {where}: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ContractError(f"{where} must be a regular non-symlink file")
    if info.st_size > _MAX_CONTRACT_BYTES:
        raise ContractError(f"{where} exceeds the {_MAX_CONTRACT_BYTES}-byte limit")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot read {where}: {error}") from error


def _load_schema_contract(path: Path, schema_name: str, where: str) -> Tuple[Dict[str, Any], bytes]:
    raw = _read_regular_bytes(path, where)
    value = strict_json_loads(raw, where)
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be a JSON object")
    schema_raw = _read_regular_bytes(_SCHEMA_ROOT / schema_name, f"{schema_name} schema")
    schema = strict_json_loads(schema_raw, f"{schema_name} schema")
    if not isinstance(schema, dict):
        raise ContractError(f"{schema_name} schema must be an object")
    _validate_schema(value, schema, schema)
    return value, raw


def _cross_engine_profile(
    path: Path,
) -> Tuple[Dict[str, Any], bytes, Tuple[Tuple[Mapping[str, Any], bytes], ...]]:
    profile_path = path.absolute()
    profile, profile_raw = _load_schema_contract(
        profile_path, "cross-engine-profile-v1.schema.json", "cross-engine profile"
    )
    contract_root = profile_path.parent.parent.resolve()
    scenario_sets: List[Tuple[Mapping[str, Any], bytes]] = []
    for index, binding in enumerate(profile["scenario_sets"]):
        bound_path = contract_root / binding["path"]
        candidate = bound_path.resolve()
        try:
            candidate.relative_to(contract_root)
        except ValueError as error:
            raise ContractError("profile scenario-set path escapes its contract root") from error
        scenario_set, raw = _load_schema_contract(
            bound_path,
            "cross-engine-scenario-set-v1.schema.json",
            f"scenario set {binding['id']}",
        )
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ContractError(f"profile scenario_sets[{index}] digest does not match")
        if scenario_set["id"] != binding["id"]:
            raise ContractError(f"profile scenario_sets[{index}] ID does not match")
        if scenario_set["activation"] != binding["activation"]:
            raise ContractError(f"profile scenario_sets[{index}] activation does not match")
        scenario_sets.append((scenario_set, raw))
    return profile, profile_raw, tuple(scenario_sets)


def _expanded_case_plan(scenario_sets: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    scenarios = [scenario for value in scenario_sets for scenario in value["scenarios"]]
    ids = [scenario["id"] for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ContractError("profile scenario sets contain duplicate scenario IDs")
    plan: List[Dict[str, Any]] = []
    for scenario in sorted(scenarios, key=lambda item: item["id"]):
        repetitions = scenario["protocol"]["measured_repetitions"]
        for matrix in scenario["matrices"]:
            dimensions = matrix["dimensions"]
            products = itertools.product(
                *(dimension["values"] for dimension in dimensions)
            )
            for cell_ordinal, values in enumerate(products):
                parameters = {
                    dimension["id"]: value
                    for dimension, value in zip(dimensions, values)
                }
                pairing_id = "openai-serving.{}.{}.c{:04d}".format(
                    scenario["id"], matrix["id"], cell_ordinal
                )
                for repetition in range(repetitions):
                    plan.append(
                        {
                            "case_id": "{}.r{:02d}".format(pairing_id, repetition),
                            "pairing_id": pairing_id,
                            "scenario_id": scenario["id"],
                            "matrix_id": matrix["id"],
                            "parameters": parameters,
                            "repetition": repetition,
                        }
                    )
    return plan


def _case_plan_sha256(plan: Sequence[Mapping[str, Any]]) -> str:
    raw = json.dumps(
        list(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    return hashlib.sha256(raw).hexdigest()


def _campaign_cases(scenario_sets: Iterable[Mapping[str, Any]]) -> Tuple[List[Mapping[str, Any]], int]:
    scenarios = [scenario for value in scenario_sets for scenario in value["scenarios"]]
    repetition_counts = {
        scenario["protocol"]["measured_repetitions"] for scenario in scenarios
    }
    if len(repetition_counts) != 1:
        raise ContractError("campaign freezer requires one profile-wide repetition count")
    cases: List[Mapping[str, Any]] = []
    for scenario in sorted(scenarios, key=lambda item: item["id"]):
        for matrix in scenario["matrices"]:
            products = itertools.product(
                *(dimension["values"] for dimension in matrix["dimensions"])
            )
            for cell_ordinal, values in enumerate(products):
                parameters = {
                    dimension["id"]: value
                    for dimension, value in zip(matrix["dimensions"], values)
                }
                pairing_id = "openai-serving.{}.{}.c{:04d}".format(
                    scenario["id"], matrix["id"], cell_ordinal
                )
                cases.append(
                    {
                        "case_id": pairing_id,
                        "pairing_id": pairing_id,
                        "scenario_id": scenario["id"],
                        "matrix_id": matrix["id"],
                        "parameters": parameters,
                        "required_capabilities": scenario["required_capabilities"],
                        "isolation_policy": scenario["isolation"]["process"],
                    }
                )
    return cases, next(iter(repetition_counts))


def _inspect_cross_engine(profile_path: Path) -> Mapping[str, Any]:
    profile, profile_raw, loaded = _cross_engine_profile(profile_path)
    scenario_sets = [value for value, _raw in loaded]
    plan = _expanded_case_plan(scenario_sets)
    return {
        "status": "valid",
        "profile_id": profile["id"],
        "profile_sha256": hashlib.sha256(profile_raw).hexdigest(),
        "measurement_surface": profile["measurement_surface"],
        "scenario_sets": [
            {
                "id": value["id"],
                "sha256": hashlib.sha256(raw).hexdigest(),
                "activation": value["activation"],
            }
            for value, raw in loaded
        ],
        "scenario_count": sum(len(value["scenarios"]) for value in scenario_sets),
        "planned_case_count": len(plan),
        "case_plan_sha256": _case_plan_sha256(plan),
    }


def _prepare_output_root(path: Path) -> Path:
    original = path.absolute()
    if original.exists() or original.is_symlink():
        try:
            original_info = original.lstat()
        except OSError as error:
            raise ContractError(f"cannot inspect cross-engine output root: {error}") from error
        if stat.S_ISLNK(original_info.st_mode):
            raise ContractError("cross-engine output root must not be a symlink")
    root = original.resolve()
    repository = _REPOSITORY_ROOT.resolve()
    if root == repository or repository in root.parents:
        raise ContractError("cross-engine output root must be outside the repository")
    if root.exists():
        try:
            info = root.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ContractError("cross-engine output root must be a real directory")
            if any(root.iterdir()):
                raise ContractError("cross-engine output root must be absent or empty")
        except OSError as error:
            raise ContractError(f"cannot inspect cross-engine output root: {error}") from error
    else:
        try:
            root.mkdir(parents=True)
        except OSError as error:
            raise ContractError(f"cannot create cross-engine output root: {error}") from error
    return root


def _write_create_only_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ContractError(f"create-only output already exists: {path}") from error
    except OSError as error:
        raise ContractError(f"cannot write create-only output {path}: {error}") from error


def _runtime_snapshot_sha256(model_root: Path) -> str:
    if not model_root.is_absolute() or not model_root.is_dir():
        raise ContractError("--model-root must be an absolute local model directory")
    rows = []
    for path in sorted(model_root.iterdir(), key=lambda item: item.name):
        if path.name in {"model-manifest.json", ".cache"}:
            continue
        if path.is_symlink() or not path.is_file():
            raise ContractError("publication model snapshot must contain regular top-level files only")
        raw = path.read_bytes()
        rows.append({"path": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    if not rows:
        raise ContractError("publication model snapshot is empty")
    return hashlib.sha256(
        (json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    raw = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise ContractError(f"cannot write publication artifact {path}: {error}") from error


def _bounded_host_command(argv: Sequence[str], *, max_bytes: int = 64 * 1024) -> str:
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ContractError(f"host admission command {argv[0]!r} failed: {error}") from error
    output = completed.stdout
    if completed.returncode != 0 or len(output.encode("utf-8")) > max_bytes:
        raise ContractError(f"host admission command {argv[0]!r} was unavailable")
    return output


def _probe_host_admission(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    if platform.system() != "Darwin":
        raise ContractError("mlx-lm publication host admission currently requires macOS")
    power = _bounded_host_command(("pmset", "-g", "batt"))
    custom = _bounded_host_command(("pmset", "-g", "custom"))
    thermal = _bounded_host_command(("pmset", "-g", "therm"))
    pressure = _bounded_host_command(("memory_pressure", "-Q"))
    swap = _bounded_host_command(("sysctl", "-n", "vm.swapusage"))
    processes = _bounded_host_command(
        ("ps", "-Ao", "%cpu=,pid=,comm="), max_bytes=256 * 1024
    )
    low_power_values = [int(value) for value in re.findall(r"lowpowermode\s+(\d+)", custom)]
    swap_match = re.search(r"used\s*=\s*([0-9.]+)([KMG])", swap)
    if not low_power_values or swap_match is None:
        raise ContractError("macOS host admission output did not match its bounded grammar")
    scale = {"K": 1024, "M": 1024**2, "G": 1024**3}[swap_match.group(2)]
    swap_bytes = int(float(swap_match.group(1)) * scale)
    process_rows = []
    for line in processes.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            cpu = float(parts[0])
            pid = int(parts[1])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        process_rows.append((cpu, pid, parts[2]))
    top_cpu, top_pid, top_command = max(process_rows, default=(0.0, 0, "none"))
    load_1m = os.getloadavg()[0]
    thermal_state = "nominal" if "No thermal warning level has been recorded" in thermal else "unknown"
    memory_pressure = "normal" if "System-wide memory free percentage:" in pressure else "unknown"
    observations = {
        "external_power": "AC Power" in power,
        "low_power_mode": any(low_power_values),
        "thermal_state": thermal_state,
        "memory_pressure": memory_pressure,
        "load_average_1m": load_1m,
        "swap_bytes": swap_bytes,
        "top_process_cpu_percent": top_cpu,
        "top_process_pid": top_pid,
        "top_process_command": top_command[:1024],
    }
    failures = []
    if policy["external_power_required"] and not observations["external_power"]:
        failures.append("external_power_required")
    if policy["low_power_mode"] == "forbidden" and observations["low_power_mode"]:
        failures.append("low_power_mode_forbidden")
    if thermal_state not in policy["allowed_thermal_states"]:
        failures.append("thermal_state_not_admitted")
    if memory_pressure not in policy["allowed_memory_pressure"]:
        failures.append("memory_pressure_not_admitted")
    if load_1m > policy["max_load_average_1m"]:
        failures.append("load_average_above_limit")
    if swap_bytes > policy["max_swap_bytes"]:
        failures.append("swap_above_limit")
    if top_cpu > policy["max_top_process_cpu_percent"]:
        failures.append("competing_process_cpu_above_limit")
    return {
        "schema_version": "turnvector.benchmark.host-admission.v1",
        "observations": observations,
        "failures": failures,
        "admitted": not failures,
    }


def _execute_serving_publication(
    *,
    profile: Mapping[str, Any],
    campaign: Any,
    targets: Sequence[Tuple[Mapping[str, Any], bytes]],
    scenario_sets: Sequence[Mapping[str, Any]],
    root: Path,
    target_checkout: Optional[Path],
    model_root: Optional[Path],
    port: Optional[int],
) -> Tuple[Mapping[str, Any], int]:
    if profile.get("claim_scope") != "absolute_single_target":
        raise ContractError("enabled execution requires an explicit absolute_single_target profile")
    if len(targets) != 1:
        raise ContractError("absolute publication execution requires exactly one enabled target")
    if target_checkout is None or model_root is None or port is None:
        raise ContractError("enabled publication requires --target-checkout, --model-root and --port")
    target_checkout = target_checkout.absolute()
    model_root = model_root.absolute()
    if not target_checkout.is_dir():
        raise ContractError("--target-checkout must be an existing absolute directory")
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise ContractError("--port must be between 1 and 65535")
    target, _target_raw = targets[0]
    if target.get("manifest_purpose") != "publication":
        raise ContractError("enabled execution requires a publication target manifest")
    if target.get("endpoint", {}).get("response_dialect") != profile.get("response_dialect"):
        raise ContractError("target response dialect does not match publication profile")
    if _runtime_snapshot_sha256(model_root) != target["model"]["snapshot_sha256"]:
        raise ContractError("runtime model snapshot digest does not match publication target")
    for relative, expected in (
        ("config.json", target["model"]["runtime_config_sha256"]),
        ("tokenizer.json", target["model"]["tokenizer_sha256"]),
    ):
        path = model_root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ContractError(f"runtime model {relative} digest does not match publication target")
    executable = target_checkout / "bin" / "mlx_lm.server"
    if not executable.is_file() or hashlib.sha256(executable.read_bytes()).hexdigest() != target["executables"][0]["sha256"]:
        raise ContractError("runtime mlx_lm.server digest does not match publication target")
    host_admission = _probe_host_admission(profile["host_admission"])
    _write_create_only_json(root / "host-admission.json", host_admission)
    if not host_admission["admitted"]:
        return {
            "status": "preflight_only",
            "contract_status": "valid",
            "capability_status": "environment_unavailable",
            "execution_status": "not_started",
            "evidence_status": "not_evaluated",
            "reason_code": host_admission["failures"][0],
            "host_admission": str(root / "host-admission.json"),
            "campaign": str(root / "campaign.json"),
            "planned_cell_count": len(campaign.cells),
        }, 4

    scenarios = {
        scenario["id"]: scenario
        for scenario_set in scenario_sets
        for scenario in scenario_set["scenarios"]
    }
    workload = HuggingFaceWorkload(str(model_root))
    executor = CommonServingExecutor(
        scenarios=scenarios,
        prompt_factory=workload.prompt,
        token_counter=workload.count,
        response_dialects={target["id"]: profile["response_dialect"]},
    )
    declared_capabilities = {
        name: disposition["status"] == "supported"
        for name, disposition in target["capabilities"].items()
    }

    def capability_probe(_target: Any, _cell: Any) -> Mapping[str, Any]:
        return {
            "capabilities": declared_capabilities,
            "profile_compatible": True,
            "environment_available": True,
            "applicable": True,
        }

    execution_root = root / "execution"
    result = CrossEngineController(
        campaign=campaign,
        targets={target["id"]: target},
        output_root=execution_root,
        scenario_executor=executor,
        capability_probe=capability_probe,
        runtime_bindings={
            target["id"]: {
                "target_checkout": str(target_checkout),
                "model_root": str(model_root),
                "port": port,
            }
        },
    ).run()

    publication_root = root / "publication"
    publication_root.mkdir(exist_ok=False)
    _write_create_only_json(publication_root / "host-admission.json", host_admission)
    cells = [cell.as_dict() for cell in result.cells]
    artifact_rows: Dict[str, List[Mapping[str, Any]]] = {
        "request_trace": [],
        "stream_events": [],
        "raw_trials": [],
        "request_metrics": [],
        "output_hashes": [],
        "host_samples": [],
        "process_audit": [],
    }
    for cell in cells:
        observations = cell["observations"]
        for name in ("request_trace", "stream_events", "raw_trials", "request_metrics", "output_hashes"):
            artifact_rows[name].extend(observations.get(name, []))
        if "host_process_evidence" in observations:
            artifact_rows["host_samples"].append(
                {"cell_id": cell["cell_id"], "evidence": observations["host_process_evidence"]}
            )
        if "listener_owner" in observations:
            artifact_rows["process_audit"].append(
                {"cell_id": cell["cell_id"], "listener_owner": observations["listener_owner"]}
            )
    for name, rows in artifact_rows.items():
        _write_jsonl(publication_root / f"{name}.jsonl", rows)
    (publication_root / "attempts.jsonl").write_bytes(result.attempts_path.read_bytes())

    completed = [cell for cell in cells if cell["execution_status"] == "completed"]
    trials = [row for cell in completed for row in cell["observations"].get("raw_trials", [])]
    failed_requests = sum(row["trial_metrics"]["failed_request_count"] for row in trials)
    goodput_ok = bool(trials) and all(row["trial_metrics"]["slo_goodput_ratio"] >= 0.95 for row in trials)
    execution_status = "completed" if len(completed) == len(cells) else "partial"
    evidence_status = "publishable" if execution_status == "completed" and failed_requests == 0 else "not_publishable"
    promotion_status = "passed" if evidence_status == "publishable" and goodput_ok else (
        "failed" if evidence_status == "publishable" else "not_evaluated"
    )
    requests = artifact_rows["request_metrics"]
    trial_metrics = [row["trial_metrics"] for row in trials]

    def summary(values: Iterable[Any]) -> Mapping[str, Optional[float]]:
        raw_values = list(values)
        present = [float(value) for value in raw_values if value is not None]
        return summarize_observations(
            present, unavailable_count=len(raw_values) - len(present)
        )

    metric_summary = {
        "successful_request_count": sum(row["valid_completed_request_count"] for row in trial_metrics),
        "failed_request_count": failed_requests,
        "input_token_count": sum(row["canonical_input_tokens"] or 0 for row in requests),
        "output_token_count": sum(row["canonical_output_tokens"] or 0 for row in requests),
        "e2e_ms": summary(row["e2e_ms"] for row in requests),
        "ttft_ms": summary(row["ttft_ms"] for row in requests),
        "stream_event_interval_ms": summary(
            value for row in requests for value in row["stream_event_interval_ms"]
        ),
        "client_post_first_output_ms_per_token": summary(
            row["client_post_first_output_ms_per_token"] for row in requests
        ),
        "request_throughput": summary(row["request_throughput"] for row in trial_metrics),
        "output_throughput": summary(row["output_throughput"] for row in trial_metrics),
        "offered_request_rate": summary(row["offered_request_rate"] for row in trial_metrics),
        "completion_rate": summary(row["completion_rate"] for row in trial_metrics),
        "output_mismatch_count": 0,
        "output_contract_violation_count": sum(
            "output_contract" in row["error_classes"] for row in requests
        ),
        "token_count_disagreement_count": sum(
            "token_count_disagreement" in row["error_classes"] for row in requests
        ),
        "slo_goodput_ratio": summary(row["slo_goodput_ratio"] for row in trial_metrics),
    }
    report = {
        "schema_version": "turnvector.benchmark.mlx-lm-serving-publication.v1",
        "campaign_id": campaign.campaign_id,
        "profile_id": profile["id"],
        "target_id": target["id"],
        "claim_scope": "absolute_single_target",
        "response_dialect": profile["response_dialect"],
        "qualification_claim": None,
        "cross_target_favorable_claim": None,
        "statuses": {
            "contract_status": "valid",
            "capability_status": "supported",
            "execution_status": execution_status,
            "evidence_status": evidence_status,
            "promotion_status": promotion_status,
            "coverage_status": "complete" if len(completed) == len(cells) else "partial",
        },
        "planned_cell_count": len(cells),
        "completed_cell_count": len(completed),
        "failed_request_count": failed_requests,
        "trial_count": len(trials),
        "metric_summary": metric_summary,
        "trial_metrics": trial_metrics,
    }
    _write_create_only_json(publication_root / "report.json", report)
    specs = [
        ArtifactSpec(name.replace("_", "-"), f"{name}.jsonl", "application/jsonl", None)
        for name in artifact_rows
    ] + [
        ArtifactSpec("attempts", "attempts.jsonl", "application/jsonl", None),
        ArtifactSpec("host-admission", "host-admission.json", "application/json", None),
        ArtifactSpec("report", "report.json", "application/json", None),
    ]
    manifest = build_artifact_manifest(publication_root, specs, campaign_id=campaign.campaign_id)
    write_artifact_manifest(publication_root, manifest)
    write_sha256s_from_manifest(publication_root, manifest)
    rendered = {
        "status": "completed" if execution_status == "completed" else "partial",
        **report["statuses"],
        "campaign": str(root / "campaign.json"),
        "publication": str(publication_root / "report.json"),
        "artifact_manifest": str(publication_root / "artifact-manifest.json"),
        "planned_cell_count": len(cells),
        "completed_cell_count": len(completed),
    }
    axes = StatusAxes(
        "valid",
        "supported",
        execution_status,
        evidence_status,
        promotion_status,
        report["statuses"]["coverage_status"],
    )
    return rendered, cross_engine_exit_code([axes])


def _run_cross_engine(
    profile_path: Path,
    target_paths: Sequence[Path],
    output_path: Path,
    *,
    target_checkout: Optional[Path] = None,
    model_root: Optional[Path] = None,
    port: Optional[int] = None,
) -> Tuple[Mapping[str, Any], int]:
    profile, profile_raw, loaded = _cross_engine_profile(profile_path)
    if not target_paths:
        raise ContractError("run-cross-engine requires at least one --target")
    targets: List[Tuple[Mapping[str, Any], bytes]] = []
    target_ids = set()
    for path in target_paths:
        target, raw = _load_schema_contract(
            path.absolute(), "cross-engine-target-v1.schema.json", f"cross-engine target {path}"
        )
        if target["id"] in target_ids:
            raise ContractError("run-cross-engine target IDs must be unique")
        target_ids.add(target["id"])
        targets.append((target, raw))

    scenario_sets = [value for value, _raw in loaded]
    case_plan = _expanded_case_plan(scenario_sets)
    cases, repetitions = _campaign_cases(scenario_sets)
    campaign = freeze_campaign(
        campaign_id=f"{profile['id']}.campaign",
        cases=cases,
        target_ids=sorted(target_ids),
        repetition_count=repetitions,
    )
    root = _prepare_output_root(output_path)
    campaign_path = root / "campaign.json"
    campaign_record = {
        "schema_version": "turnvector.benchmark.cross-engine-campaign.v1",
        "profile": {
            "id": profile["id"],
            "sha256": hashlib.sha256(profile_raw).hexdigest(),
        },
        "scenario_sets": [
            {"id": value["id"], "sha256": hashlib.sha256(raw).hexdigest()}
            for value, raw in loaded
        ],
        "targets": [
            {"id": value["id"], "sha256": hashlib.sha256(raw).hexdigest(), "enabled": value["enabled"]}
            for value, raw in sorted(targets, key=lambda item: item[0]["id"])
        ],
        "case_plan_sha256": _case_plan_sha256(case_plan),
        "case_plan": case_plan,
        "campaign": campaign.as_dict(),
    }
    # This frozen record exists before any adapter resolution or target startup.
    _write_create_only_json(campaign_path, campaign_record)

    disabled = sorted(target["id"] for target, _raw in targets if not target["enabled"])
    if disabled:
        rendered = {
            "status": "preflight_only",
            "contract_status": "valid",
            "capability_status": "profile_incompatible",
            "execution_status": "not_started",
            "evidence_status": "not_evaluated",
            "reason_code": "target_not_enabled",
            "disabled_targets": disabled,
            "campaign": str(campaign_path),
            "planned_cell_count": len(campaign.cells),
        }
        return rendered, 4

    return _execute_serving_publication(
        profile=profile,
        campaign=campaign,
        targets=targets,
        scenario_sets=scenario_sets,
        root=root,
        target_checkout=target_checkout,
        model_root=model_root,
        port=port,
    )


def _status_axes(evidence: Mapping[str, Any]) -> StatusAxes:
    statuses = evidence["statuses"]
    return StatusAxes(
        statuses["contract_status"],
        statuses["capability_status"],
        statuses["execution_status"],
        statuses["evidence_status"],
        statuses["promotion_status"],
        statuses["coverage_status"],
    )


def _evidence_paths(root: Path) -> Tuple[Path, ...]:
    original = root.absolute()
    if original.is_symlink():
        raise ContractError("cross-engine evidence root must not be a symlink")
    resolved = original.resolve()
    if resolved.is_file():
        return (original,)
    if not resolved.is_dir():
        raise ContractError("cross-engine evidence must be a file or directory")
    candidates: List[Path] = []
    direct = resolved / "evidence.json"
    if direct.is_file():
        candidates.append(direct)
    candidates.extend(sorted(resolved.glob("*.evidence.json")))
    candidates.extend(sorted(resolved.glob("*/evidence.json")))
    unique = tuple(dict.fromkeys(path.absolute() for path in candidates))
    if not unique:
        raise ContractError("cross-engine evidence root contains no evidence.json files")
    return unique


def _load_cross_engine_evidence(root: Path) -> Tuple[Mapping[str, Any], ...]:
    values: List[Mapping[str, Any]] = []
    for path in _evidence_paths(root):
        value, _raw = _load_schema_contract(
            path, "cross-engine-evidence-v1.schema.json", f"cross-engine evidence {path}"
        )
        _status_axes(value)
        values.append(value)
    if len(values) < 2:
        raise ContractError("compare-cross-engine requires evidence from at least two targets")
    target_ids = [value["target"]["id"] for value in values]
    if len(target_ids) != len(set(target_ids)):
        raise ContractError("cross-engine evidence target IDs must be unique")
    identity_fields = ("campaign_id", "case_plan_sha256")
    for field in identity_fields:
        if len({value[field] for value in values}) != 1:
            raise ContractError(f"cross-engine evidence disagrees on {field}")
    for field in ("profile", "scenario_sets"):
        encoded = {canonical_json_bytes(value[field]) for value in values}
        if len(encoded) != 1:
            raise ContractError(f"cross-engine evidence disagrees on {field}")
    return tuple(sorted(values, key=lambda value: value["target"]["id"]))


def _available_rows(evidence: Mapping[str, Any]) -> Dict[Tuple[str, str], Mapping[str, Any]]:
    rows: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for row in evidence["rows"]:
        if row["eligibility"] != "available":
            continue
        key = (row["pairing_id"], row["metric_id"])
        if key in rows:
            raise ContractError("cross-engine evidence contains duplicate available metric rows")
        rows[key] = row
    return rows


def _compare_cross_engine(
    evidence_root: Path,
    require_common_core: bool,
    require_promotion: bool,
) -> Tuple[Mapping[str, Any], int]:
    evidence = _load_cross_engine_evidence(evidence_root)
    pair_reports: List[Mapping[str, Any]] = []
    for left, right in itertools.combinations(evidence, 2):
        left_rows = _available_rows(left)
        right_rows = _available_rows(right)
        requested = sorted(
            {pairing for pairing, _metric in left_rows}
            | {pairing for pairing, _metric in right_rows}
        )
        left_supported = {
            pairing: "supported"
            for pairing, _metric in left_rows
            if left["statuses"]["capability_status"] == "supported"
        }
        right_supported = {
            pairing: "supported"
            for pairing, _metric in right_rows
            if right["statuses"]["capability_status"] == "supported"
        }
        intersection = coverage_intersection(
            requested, left_supported, right_supported
        )
        if require_common_core and intersection.coverage_status == "zero_common_cells":
            raise ContractError("required common core has zero common cells")

        comparison_rows = []
        metric_summaries: Dict[str, Mapping[str, Any]] = {}
        metrics = sorted({metric for _pairing, metric in left_rows} & {metric for _pairing, metric in right_rows})
        for metric in metrics:
            cells = tuple(
                pairing
                for pairing in intersection.common
                if (pairing, metric) in left_rows and (pairing, metric) in right_rows
            )
            if not cells:
                continue
            metric_intersection = coverage_intersection(
                cells,
                {cell: "supported" for cell in cells},
                {cell: "supported" for cell in cells},
            )
            left_values = {cell: left_rows[(cell, metric)]["value"] for cell in cells}
            right_values = {cell: right_rows[(cell, metric)]["value"] for cell in cells}
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
                for value in (*left_values.values(), *right_values.values())
            ):
                continue
            semantic_claims = {
                left_rows[(cell, metric)]["semantic_claim"] for cell in cells
            } | {
                right_rows[(cell, metric)]["semantic_claim"] for cell in cells
            }
            if len(semantic_claims) != 1:
                raise ContractError("paired evidence disagrees on semantic claim")
            rows = paired_rows(
                metric_intersection,
                left["target"]["id"],
                right["target"]["id"],
                metric,
                left_values,
                right_values,
                comparison_form="paired_delta",
                semantic_claim=next(iter(semantic_claims)),
            )
            comparison_rows.extend(row.as_dict() for row in rows)
            metric_summaries[metric] = comparison_summary(rows)
        pair_reports.append(
            {
                "left_target_id": left["target"]["id"],
                "right_target_id": right["target"]["id"],
                "coverage": intersection.as_dict(),
                "comparison_rows": comparison_rows,
                "metric_summaries": metric_summaries,
            }
        )
    rendered = {
        "status": "valid",
        "campaign_id": evidence[0]["campaign_id"],
        "evidence_count": len(evidence),
        "pairs": pair_reports,
        "qualification_claim": None,
    }
    return rendered, cross_engine_exit_code(
        [_status_axes(value) for value in evidence],
        require_promotion=require_promotion,
    )


def _native_plan(path: Path) -> NativeInferencePlan:
    value, _raw = _load_schema_contract(
        path.absolute(),
        "cross-engine-native-profile-v1.schema.json",
        "cross-engine native profile",
    )
    return NativeInferencePlan(
        value["plan_id"],
        value["tool_id"],
        value["model_sha256"],
        value["tokenizer_sha256"],
        tuple(value["prompt_tokens"]),
        value["output_tokens"],
        value["warmups"],
        value["repetitions"],
        value["cooldown_seconds"],
        value["sampling_policy"],
    )


def _inspect_native_profile(path: Path) -> Mapping[str, Any]:
    plan = _native_plan(path)
    return {
        "status": "valid",
        "profile": plan.as_dict(),
        "profile_sha256": plan.sha256,
        "command_prefix": list(plan.command_prefix),
        "planned_trial_count": len(plan.prompt_tokens) * plan.repetitions,
        "measurement_surface": "native_inference",
    }


def _validate_native_profile(path: Path, trials_path: Path) -> Mapping[str, Any]:
    plan = _native_plan(path)
    trials = parse_native_trials(trials_path.resolve(), plan)
    return {
        "status": "valid",
        "profile_sha256": plan.sha256,
        "trial_count": len(trials),
        "summary": summarize_native_trials(trials),
        "measurement_surface": "native_inference",
    }


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
    validate_performance.add_argument(
        "--require-promotion",
        action="store_true",
        help="return exit 5 when publishable evidence fails its promotion gates",
    )

    inspect_gateway = subparsers.add_parser(
        "inspect-gateway-validation",
        help="inspect the Gateway lifecycle and Unix transport CasePlan",
    )
    inspect_gateway.add_argument("--contract", type=Path, required=True)
    inspect_gateway.add_argument("--target-repo", type=Path)

    validate_gateway = subparsers.add_parser(
        "validate-gateway-validation",
        help="independently validate one Gateway evidence artifact",
    )
    validate_gateway.add_argument("--contract", type=Path, required=True)
    validate_gateway.add_argument("--evidence", type=Path, required=True)
    validate_gateway.add_argument("--output", type=Path)

    inspect_cross_engine = subparsers.add_parser(
        "inspect-cross-engine",
        help="inspect a frozen cross-engine serving profile without side effects",
    )
    inspect_cross_engine.add_argument("--profile", type=Path, required=True)

    run_cross_engine = subparsers.add_parser(
        "run-cross-engine",
        help="freeze, preflight, and execute an enabled cross-engine publication campaign",
    )
    run_cross_engine.add_argument("--profile", type=Path, required=True)
    run_cross_engine.add_argument("--target", type=Path, action="append", required=True)
    run_cross_engine.add_argument("--output", type=Path, required=True)
    run_cross_engine.add_argument(
        "--target-checkout",
        type=Path,
        help="absolute installed target environment for a single enabled publication target",
    )
    run_cross_engine.add_argument(
        "--model-root",
        type=Path,
        help="absolute local model snapshot for a single enabled publication target",
    )
    run_cross_engine.add_argument("--port", type=int)

    compare_cross_engine = subparsers.add_parser(
        "compare-cross-engine",
        help="independently validate and compare cross-engine evidence",
    )
    compare_cross_engine.add_argument("--evidence", type=Path, required=True)
    compare_cross_engine.add_argument("--require-common-core", action="store_true")
    compare_cross_engine.add_argument(
        "--require-promotion",
        action="store_true",
        help="return exit 5 when publishable evidence fails required promotion gates",
    )

    promote_cross_engine = subparsers.add_parser(
        "promote-cross-engine-baseline",
        help="create an immutable cross-engine baseline receipt",
    )
    promote_cross_engine.add_argument("--registry", type=Path, required=True)
    promote_cross_engine.add_argument("--baseline-id", required=True)
    promote_cross_engine.add_argument("--evidence", type=Path, required=True)
    promote_cross_engine.add_argument("--authority-id", required=True)
    for identity_name in (
        "profile-sha256",
        "scenario-set-sha256",
        "target-sha256",
        "model-sha256",
        "physical-host-sha256",
    ):
        promote_cross_engine.add_argument(f"--{identity_name}", required=True)
    promote_cross_engine.add_argument("--superseded-baseline-sha256")
    promote_cross_engine.add_argument("--promoted-at")

    inspect_native = subparsers.add_parser(
        "inspect-cross-engine-native",
        help="inspect a separately authorized cross-engine native profile",
    )
    inspect_native.add_argument("--profile", type=Path, required=True)

    validate_native = subparsers.add_parser(
        "validate-cross-engine-native",
        help="validate raw native trials against their frozen native profile",
    )
    validate_native.add_argument("--profile", type=Path, required=True)
    validate_native.add_argument("--trials", type=Path, required=True)

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
        if args.command == "inspect-cross-engine":
            print(json.dumps(_inspect_cross_engine(args.profile), sort_keys=True))
            return 0
        if args.command == "run-cross-engine":
            rendered, exit_code = _run_cross_engine(
                args.profile,
                args.target,
                args.output,
                target_checkout=args.target_checkout,
                model_root=args.model_root,
                port=args.port,
            )
            print(json.dumps(rendered, sort_keys=True))
            return exit_code
        if args.command == "compare-cross-engine":
            rendered, exit_code = _compare_cross_engine(
                args.evidence,
                args.require_common_core,
                args.require_promotion,
            )
            print(json.dumps(rendered, sort_keys=True))
            return exit_code
        if args.command == "promote-cross-engine-baseline":
            receipt = promote_baseline(
                args.registry,
                args.baseline_id,
                args.evidence,
                authority_id=args.authority_id,
                identities={
                    "profile_sha256": args.profile_sha256,
                    "scenario_set_sha256": args.scenario_set_sha256,
                    "target_sha256": args.target_sha256,
                    "model_sha256": args.model_sha256,
                    "physical_host_sha256": args.physical_host_sha256,
                },
                superseded_baseline_sha256=args.superseded_baseline_sha256,
                promoted_at=args.promoted_at,
            )
            print(
                json.dumps(
                    {
                        "status": "created",
                        "baseline_id": args.baseline_id,
                        "receipt_sha256": receipt.sha256,
                        "receipt": str(
                            args.registry.resolve()
                            / args.baseline_id
                            / "receipt.json"
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "inspect-cross-engine-native":
            print(json.dumps(_inspect_native_profile(args.profile), sort_keys=True))
            return 0
        if args.command == "validate-cross-engine-native":
            print(
                json.dumps(
                    _validate_native_profile(args.profile, args.trials),
                    sort_keys=True,
                )
            )
            return 0
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
            exit_code = {
                "publishable": 0,
                "not_publishable": 3,
                "unsupported": 4,
            }[report["status"]]
            if (
                args.require_promotion
                and exit_code == 0
                and report["promotion_status"] not in {"passed", "not_applicable"}
            ):
                return 5
            return exit_code
        if args.command == "inspect-gateway-validation":
            contract = load_gateway_validation_contract(args.contract)
            print(json.dumps(contract.inspect(args.target_repo), sort_keys=True))
            return 0
        if args.command == "validate-gateway-validation":
            contract = load_gateway_validation_contract(args.contract)
            report = contract.validate_artifact(args.evidence)
            if args.output is not None:
                output = args.output.resolve()
                if output.exists():
                    raise FileExistsError(
                        f"Gateway validation output already exists: {output}"
                    )
                output.mkdir(parents=True)
                write_json(output / "report.json", report)
                write_checksums(output)
                rendered = {
                    "status": report["status"],
                    "report": str(output / "report.json"),
                }
            else:
                rendered = report
            print(json.dumps(rendered, sort_keys=True))
            return {
                "publishable": 0,
                "not_publishable": 3,
                "not_claimable_fixture": 4,
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
