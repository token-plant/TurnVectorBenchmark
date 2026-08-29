from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
PROFILE_PATH = ROOT / "profiles" / "cross-engine-openai-serving-v1.json"
SCENARIO_PATHS = (
    ROOT / "scenarios" / "openai-serving-common-v1.json",
    ROOT / "scenarios" / "openai-serving-capabilities-v1.json",
)
TARGET_PATHS = (
    ROOT / "targets" / "turnvector-openai-v1.json",
    ROOT / "targets" / "ax-engine-openai-v1.json",
    ROOT / "targets" / "mlx-lm-openai-v1.json",
    ROOT / "targets" / "llama-cpp-openai-v1.json",
)
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
EXPECTED_PLAN_SHA256 = "bb3073ba1dd5ee7413770fc559dc7b03fa60df6e59f997b7884a04c0ebdec699"


class ValidationError(ValueError):
    pass


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture must be a JSON object: {}".format(path))
    return value


def json_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def resolve_pointer(root: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    if not reference.startswith("#/"):
        raise ValidationError("only local JSON pointers are used in these schemas")
    value: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    if not isinstance(value, dict):
        raise ValidationError("schema reference does not resolve to an object")
    return value


def type_matches(value: Any, expected: str) -> bool:
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
    raise ValidationError("unsupported test-validator type: {}".format(expected))


def validate(instance: Any, schema: Any, root: Mapping[str, Any] = None, path: str = "$") -> None:
    """Validate the Draft 2020-12 subset intentionally used by this contract slice.

    The repository has no runtime jsonschema dependency. Keeping this strict validator in
    tests exercises examples and negative mutations on Python 3.9 without changing
    requirements. The separate schema-shape test also checks local refs and object closure.
    """
    if root is None:
        if not isinstance(schema, dict):
            raise ValidationError("root schema must be an object")
        root = schema
    if schema is False:
        raise ValidationError("{} rejected by false schema".format(path))
    if schema is True:
        return
    if not isinstance(schema, dict):
        raise ValidationError("{} has a malformed schema".format(path))
    if "$ref" in schema:
        validate(instance, resolve_pointer(root, schema["$ref"]), root, path)
        return

    if "allOf" in schema:
        for branch in schema["allOf"]:
            validate(instance, branch, root, path)
    if "anyOf" in schema:
        successes = 0
        for branch in schema["anyOf"]:
            try:
                validate(instance, branch, root, path)
                successes += 1
            except ValidationError:
                pass
        if successes == 0:
            raise ValidationError("{} does not match anyOf".format(path))
    if "oneOf" in schema:
        successes = 0
        for branch in schema["oneOf"]:
            try:
                validate(instance, branch, root, path)
                successes += 1
            except ValidationError:
                pass
        if successes != 1:
            raise ValidationError("{} matches {} oneOf branches".format(path, successes))
    if "not" in schema:
        try:
            validate(instance, schema["not"], root, path)
        except ValidationError:
            pass
        else:
            raise ValidationError("{} matches a forbidden schema".format(path))

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(type_matches(instance, item) for item in expected_types):
            raise ValidationError("{} has the wrong type".format(path))
    if "const" in schema and not json_equal(instance, schema["const"]):
        raise ValidationError("{} does not equal const".format(path))
    if "enum" in schema and not any(json_equal(instance, item) for item in schema["enum"]):
        raise ValidationError("{} is outside enum".format(path))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [field for field in required if field not in instance]
        if missing:
            raise ValidationError("{} is missing {}".format(path, missing))
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], root, "{}.{}".format(path, key))
            elif additional is False:
                raise ValidationError("{}.{} is unknown".format(path, key))
            elif isinstance(additional, dict):
                validate(value, additional, root, "{}.{}".format(path, key))
        if "propertyNames" in schema:
            for key in instance:
                validate(key, schema["propertyNames"], root, "{}.<key>".format(path))
        if len(instance) < schema.get("minProperties", 0):
            raise ValidationError("{} has too few properties".format(path))
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise ValidationError("{} has too many properties".format(path))

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise ValidationError("{} has too few items".format(path))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise ValidationError("{} has too many items".format(path))
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise ValidationError("{} has duplicate items".format(path))
        prefix_items = schema.get("prefixItems", [])
        for index, item_schema in enumerate(prefix_items):
            if index < len(instance):
                validate(instance[index], item_schema, root, "{}[{}]".format(path, index))
        item_schema = schema.get("items")
        if item_schema is not None:
            start = len(prefix_items) if prefix_items else 0
            for index in range(start, len(instance)):
                validate(instance[index], item_schema, root, "{}[{}]".format(path, index))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise ValidationError("{} is too short".format(path))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ValidationError("{} is too long".format(path))
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ValidationError("{} does not match pattern".format(path))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ValidationError("{} is below minimum".format(path))
        if "maximum" in schema and instance > schema["maximum"]:
            raise ValidationError("{} is above maximum".format(path))
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise ValidationError("{} is below exclusiveMinimum".format(path))
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise ValidationError("{} is above exclusiveMaximum".format(path))


def assert_schema_closed(test: unittest.TestCase, node: Any, root: Mapping[str, Any], path: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            test.assertIs(node.get("additionalProperties"), False, path)
            properties = node.get("properties", {})
            test.assertTrue(
                set(node.get("required", [])).issubset(set(properties)), path
            )
        if "$ref" in node:
            resolve_pointer(root, node["$ref"])
        for key, value in node.items():
            if key not in ("const", "enum"):
                assert_schema_closed(test, value, root, "{}/{}".format(path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            assert_schema_closed(test, value, root, "{}[{}]".format(path, index))


def expand_plan(scenario_sets: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    scenarios = [scenario for value in scenario_sets for scenario in value["scenarios"]]
    plan: List[Dict[str, Any]] = []
    for scenario in sorted(scenarios, key=lambda item: item["id"]):
        repetitions = scenario["protocol"]["measured_repetitions"]
        for matrix in scenario["matrices"]:  # declared order
            dimensions = matrix["dimensions"]  # declared order
            products = itertools.product(*(dimension["values"] for dimension in dimensions))
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


def plan_digest(plan: Sequence[Mapping[str, Any]]) -> str:
    encoded = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CrossEngineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile_schema = load_json(SCHEMA_DIR / "cross-engine-profile-v1.schema.json")
        cls.scenario_schema = load_json(SCHEMA_DIR / "cross-engine-scenario-set-v1.schema.json")
        cls.target_schema = load_json(SCHEMA_DIR / "cross-engine-target-v1.schema.json")
        cls.profile = load_json(PROFILE_PATH)
        cls.scenario_sets = [load_json(path) for path in SCENARIO_PATHS]
        cls.targets = [load_json(path) for path in TARGET_PATHS]

    def assert_invalid(self, value: Any, schema: Mapping[str, Any]) -> None:
        with self.assertRaises(ValidationError):
            validate(value, schema)

    def test_all_schemas_are_draft_2020_12_closed_and_refs_resolve(self) -> None:
        paths = sorted(SCHEMA_DIR.glob("cross-engine-*.schema.json")) + sorted(
            SCHEMA_DIR.glob("openai-serving-*.schema.json")
        )
        self.assertEqual(len(paths), 14)
        for path in paths:
            with self.subTest(path=path.name):
                schema = load_json(path)
                self.assertEqual(schema["$schema"], DRAFT_2020_12)
                self.assertEqual(
                    schema["$id"],
                    "https://token-plant.github.io/TurnVectorBenchmark/schemas/{}".format(path.name),
                )
                assert_schema_closed(self, schema, schema)

    def test_profile_scenario_and_four_target_examples_validate(self) -> None:
        validate(self.profile, self.profile_schema)
        for scenario_set in self.scenario_sets:
            validate(scenario_set, self.scenario_schema)
        for target in self.targets:
            validate(target, self.target_schema)
        self.assertEqual(
            [target["engine_family"] for target in self.targets],
            ["turnvector", "ax-engine", "mlx-lm", "llama.cpp"],
        )
        self.assertTrue(all(target["manifest_purpose"] == "example" for target in self.targets))
        self.assertTrue(all(target["enabled"] is False for target in self.targets))

    def test_exact_unknown_missing_and_mistyped_fields_fail_closed(self) -> None:
        samples = (
            (self.profile, self.profile_schema, "measurement_surface"),
            (self.scenario_sets[0], self.scenario_schema, "activation"),
            (self.targets[0], self.target_schema, "engine_family"),
        )
        for original, schema, required_field in samples:
            with self.subTest(kind=required_field):
                unknown = copy.deepcopy(original)
                unknown["unexpected"] = True
                self.assert_invalid(unknown, schema)

                missing = copy.deepcopy(original)
                del missing[required_field]
                self.assert_invalid(missing, schema)

                mistyped = copy.deepcopy(original)
                mistyped[required_field] = 1
                self.assert_invalid(mistyped, schema)

        nested_unknown = copy.deepcopy(self.scenario_sets[0])
        nested_unknown["scenarios"][0]["protocol"]["retry_count"] = 1
        self.assert_invalid(nested_unknown, self.scenario_schema)

        nested_missing = copy.deepcopy(self.targets[0])
        del nested_missing["model"]["tokenizer_sha256"]
        self.assert_invalid(nested_missing, self.target_schema)

        nested_mistyped = copy.deepcopy(self.profile)
        nested_mistyped["host_admission"]["max_swap_bytes"] = False
        self.assert_invalid(nested_mistyped, self.profile_schema)

    def test_identifier_and_digest_grammar_is_exact(self) -> None:
        bad_id = copy.deepcopy(self.targets[0])
        bad_id["id"] = "TurnVector OpenAI"
        self.assert_invalid(bad_id, self.target_schema)

        for bad_digest in ("a" * 63, "A" * 64, "g" * 64, 7):
            with self.subTest(digest=bad_digest):
                bad = copy.deepcopy(self.targets[0])
                bad["model"]["snapshot_sha256"] = bad_digest
                self.assert_invalid(bad, self.target_schema)

    def test_profile_binds_exact_scenario_paths_digests_and_activations(self) -> None:
        refs = self.profile["scenario_sets"]
        self.assertEqual([item["id"] for item in refs], [value["id"] for value in self.scenario_sets])
        self.assertEqual([item["path"] for item in refs], [str(path.relative_to(ROOT)) for path in SCENARIO_PATHS])
        self.assertEqual([item["sha256"] for item in refs], [digest(path) for path in SCENARIO_PATHS])
        self.assertEqual([item["activation"] for item in refs], [value["activation"] for value in self.scenario_sets])

    def test_plan_expansion_order_count_ids_and_digest_are_frozen(self) -> None:
        plan = expand_plan(self.scenario_sets)
        self.assertEqual(sum(len(value["scenarios"]) for value in self.scenario_sets), 13)
        self.assertEqual(len(plan), 132)
        common_ids = {
            scenario["id"]
            for scenario in self.scenario_sets[0]["scenarios"]
        }
        self.assertEqual(sum(row["scenario_id"] in common_ids for row in plan), 48)
        self.assertEqual(plan_digest(plan), EXPECTED_PLAN_SHA256)
        self.assertEqual(
            plan[0],
            {
                "case_id": "openai-serving.cancellation-and-disconnect.workload.c0000.r00",
                "pairing_id": "openai-serving.cancellation-and-disconnect.workload.c0000",
                "scenario_id": "cancellation-and-disconnect",
                "matrix_id": "workload",
                "parameters": {"termination_mode": "cancel"},
                "repetition": 0,
            },
        )
        self.assertEqual(
            plan[-1]["case_id"],
            "openai-serving.single-client-streaming.workload.c0001.r02",
        )
        self.assertEqual(
            [row["parameters"] for row in plan if row["scenario_id"] == "mtp-acceleration"][::3],
            [
                {"route": "direct", "prompt_suite": "short"},
                {"route": "direct", "prompt_suite": "medium"},
                {"route": "mtp", "prompt_suite": "short"},
                {"route": "mtp", "prompt_suite": "medium"},
            ],
        )

    def test_target_support_plan_is_complete_ordered_and_capability_derived(self) -> None:
        scenario_ids = sorted(
            scenario["id"]
            for scenario_set in self.scenario_sets
            for scenario in scenario_set["scenarios"]
        )
        common_ids = {scenario["id"] for scenario in self.scenario_sets[0]["scenarios"]}
        for target in self.targets:
            with self.subTest(target=target["id"]):
                dispositions = target["scenario_support"]
                self.assertEqual([item["scenario_id"] for item in dispositions], scenario_ids)
                self.assertTrue(
                    all(
                        item["status"] == "supported" and item["reason_code"] is None
                        for item in dispositions
                        if item["scenario_id"] in common_ids
                    )
                )
                self.assertEqual(
                    target["route_mapper_version"] is not None,
                    target["capabilities"]["route_reporting"]["status"] == "supported",
                )

    def test_openai_endpoint_request_stream_and_error_contracts(self) -> None:
        endpoint_schema = load_json(SCHEMA_DIR / "openai-serving-endpoint-v1.schema.json")
        endpoint = {
            "protocol_family": "openai-compatible",
            "protocol_version": "turnvector.benchmark.openai-serving.v1",
            "transport": "http",
            "base_url": "http://127.0.0.1:31418/v1",
            "api_flavor": "chat_completions",
            "stream_format": "sse-data-json",
            "model_ids": ["qwen3-example"],
            "process_ids": [1234],
            "capability_report_sha256": "a" * 64,
            "authentication_env_var": None,
        }
        validate(endpoint, endpoint_schema)
        for bad_url in (
            "http://localhost:31418/v1",
            "http://192.168.1.1:31418/v1",
            "http://user@127.0.0.1:31418/v1",
            "http://127.0.0.1:31418/v1#fragment",
            "https://127.0.0.1:31418/v1",
        ):
            bad = copy.deepcopy(endpoint)
            bad["base_url"] = bad_url
            self.assert_invalid(bad, endpoint_schema)
        for forbidden in (
            "data_plane",
            "turnvector.data-plane",
            "unix_stream",
            "socket_path",
            "descriptor_sha256",
            "full_implementation_status",
        ):
            bad = copy.deepcopy(endpoint)
            bad[forbidden] = "forbidden"
            self.assert_invalid(bad, endpoint_schema)

        models_schema = load_json(SCHEMA_DIR / "openai-serving-models-v1.schema.json")
        models = {
            "schema_version": "turnvector.benchmark.openai-serving-models.v1",
            "models": [
                {
                    "id": "/models/Qwen3-0.6B-4bit",
                    "owned_by": None,
                    "created": 1,
                    "extension_fields": [],
                    "record_sha256": "f" * 64,
                }
            ],
            "http_version": "HTTP/1.0",
            "response_bytes": 192,
            "response_sha256": "e" * 64,
        }
        validate(models, models_schema)
        bad_models = copy.deepcopy(models)
        bad_models["models"][0]["id"] = "bad\nmodel"
        self.assert_invalid(bad_models, models_schema)

        request_schema = load_json(SCHEMA_DIR / "openai-serving-request-v1.schema.json")
        request = {
            "model": "qwen3-example",
            "messages": [
                {"role": "system", "content": "Answer exactly."},
                {"role": "user", "content": "Hello"},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0,
            "top_p": 1,
            "n": 1,
            "seed": 7,
            "max_completion_tokens": 64,
        }
        validate(request, request_schema)
        both_bounds = copy.deepcopy(request)
        both_bounds["max_tokens"] = 64
        self.assert_invalid(both_bounds, request_schema)
        no_bound = copy.deepcopy(request)
        del no_bound["max_completion_tokens"]
        self.assert_invalid(no_bound, request_schema)
        tools = copy.deepcopy(request)
        tools["tools"] = []
        self.assert_invalid(tools, request_schema)
        assistant = copy.deepcopy(request)
        assistant["messages"][1]["role"] = "assistant"
        self.assert_invalid(assistant, request_schema)

        stream_schema = load_json(SCHEMA_DIR / "openai-serving-stream-event-v1.schema.json")
        stream_event = {
            "schema_version": "turnvector.benchmark.openai-serving-stream-event.v1",
            "request_id": "request-1",
            "sequence": 0,
            "receipt_ns": 100,
            "kind": "content_delta",
            "role": None,
            "reasoning": None,
            "content": "hello",
            "finish_reason": None,
            "usage": None,
            "raw_data_sha256": "b" * 64,
        }
        validate(stream_event, stream_schema)
        bad_event = copy.deepcopy(stream_event)
        bad_event["finish_reason"] = "stop"
        self.assert_invalid(bad_event, stream_schema)

        error_schema = load_json(SCHEMA_DIR / "openai-serving-error-v1.schema.json")
        error = {
            "schema_version": "turnvector.benchmark.openai-serving-error.v1",
            "request_id": "request-1",
            "receipt_ns": 100,
            "http_status": 429,
            "error": {"code": "rate_limit", "message": "bounded", "type": None, "param": None},
            "body_sha256": "c" * 64,
            "body_truncated": False,
        }
        validate(error, error_schema)
        bad_error = copy.deepcopy(error)
        del bad_error["error"]["message"]
        self.assert_invalid(bad_error, error_schema)

    def test_lifecycle_artifact_and_evidence_contracts_fail_closed(self) -> None:
        lifecycle_schema = load_json(SCHEMA_DIR / "cross-engine-lifecycle-v1.schema.json")
        hello = {
            "kind": "hello",
            "protocol_version": "turnvector.benchmark.cross-engine-lifecycle.v1",
            "request_id": "request-1",
            "payload": {"requested_protocol": "turnvector.benchmark.cross-engine-lifecycle.v1"},
        }
        validate(hello, lifecycle_schema)
        hello_ack = {
            "kind": "hello_ack",
            "protocol_version": "turnvector.benchmark.cross-engine-lifecycle.v1",
            "request_id": "request-1",
            "status": "ok",
            "payload": {
                "adapter_id": "fixture-adapter-v1",
                "adapter_version": "v1",
                "target_family": "fixture",
                "lifecycle_capabilities": [
                    "prepare_session",
                    "start_target",
                    "describe_endpoint",
                    "reset_state",
                    "stop_target",
                    "shutdown",
                ],
            },
            "error": None,
        }
        validate(hello_ack, lifecycle_schema)
        ambiguous = copy.deepcopy(hello_ack)
        ambiguous["error"] = {"code": "internal_error", "message": "no"}
        self.assert_invalid(ambiguous, lifecycle_schema)
        bad_line = copy.deepcopy(hello)
        bad_line["payload"]["proxy_request"] = True
        self.assert_invalid(bad_line, lifecycle_schema)

        artifact_schema = load_json(SCHEMA_DIR / "cross-engine-artifact-manifest-v1.schema.json")
        artifact = {
            "schema_version": "turnvector.benchmark.cross-engine-artifact-manifest.v1",
            "campaign_id": "campaign-1",
            "per_file_byte_limit": 67108864,
            "total_byte_limit": 536870912,
            "artifact_count": 1,
            "present_artifact_count": 1,
            "total_size_bytes": 0,
            "artifacts": [
                {
                    "id": "raw-trials",
                    "path": "raw/raw_trials.jsonl",
                    "media_type": "application/jsonl",
                    "schema_type": None,
                    "custody": "benchmark_measurement",
                    "required": True,
                    "ordinal": 1,
                    "present": True,
                    "size_bytes": 0,
                    "sha256": "d" * 64,
                }
            ],
        }
        validate(artifact, artifact_schema)
        for attack in ("../raw.jsonl", "/tmp/raw.jsonl", "raw/../../x", "raw/./x", "raw//x"):
            bad = copy.deepcopy(artifact)
            bad["artifacts"][0]["path"] = attack
            self.assert_invalid(bad, artifact_schema)

        evidence_schema = load_json(SCHEMA_DIR / "cross-engine-evidence-v1.schema.json")
        evidence = {
            "schema_version": "turnvector.benchmark.cross-engine-evidence.v1",
            "campaign_id": "campaign-1",
            "profile": {"id": "cross-engine-openai-serving-v1", "sha256": "1" * 64},
            "scenario_sets": [{"id": "openai-serving-common-v1", "sha256": "2" * 64}],
            "target": {"id": "turnvector-openai-v1", "sha256": "3" * 64},
            "session": {
                "id": "session-1",
                "benchmark_revision": "4" * 40,
                "benchmark_dirty": False,
                "target_revision_before": "5" * 40,
                "target_revision_after": "5" * 40,
                "target_dirty_before": False,
                "target_dirty_after": False,
                "started_at": "2026-08-29T00:00:00Z",
                "finished_at": "2026-08-29T00:01:00Z",
                "host_fingerprint_sha256": "6" * 64,
            },
            "case_plan_sha256": EXPECTED_PLAN_SHA256,
            "statuses": {
                "contract_status": "valid",
                "capability_status": "supported",
                "execution_status": "completed",
                "evidence_status": "publishable",
                "promotion_status": "not_applicable",
                "coverage_status": "complete",
            },
            "rows": [
                {
                    "case_id": "case-1",
                    "pairing_id": "pair-1",
                    "metric_id": "ttft_ms",
                    "unit": "milliseconds",
                    "value": 1.0,
                    "eligibility": "available",
                    "measurement_surface": "openai_serving",
                    "comparison_form": "absolute",
                    "semantic_claim": "serving",
                    "observation_level": "client_only",
                    "provenance_class": "benchmark_measurement",
                    "workload_contract": "same_api_workload",
                    "model_equivalence_class": "shape_matched",
                    "raw_artifact_ids": ["raw-trials"],
                }
            ],
            "artifacts": {"id": "artifact-manifest", "sha256": "7" * 64},
        }
        validate(evidence, evidence_schema)
        unavailable_with_value = copy.deepcopy(evidence)
        unavailable_with_value["rows"][0]["eligibility"] = "unavailable"
        self.assert_invalid(unavailable_with_value, evidence_schema)
        for forbidden in ("full_implementation_status", "qualification_lanes", "data_plane"):
            bad = copy.deepcopy(evidence)
            bad[forbidden] = "passed"
            self.assert_invalid(bad, evidence_schema)


if __name__ == "__main__":
    unittest.main()
