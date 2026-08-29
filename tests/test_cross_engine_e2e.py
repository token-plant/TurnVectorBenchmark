from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.cross_engine.openai_server import FIXTURE_MODEL, FixtureOpenAIServer
from turnvector_benchmark.cross_engine.artifacts import (
    ArtifactSpec,
    build_artifact_manifest,
    load_artifact_manifest,
    write_artifact_manifest,
    write_sha256s_from_manifest,
)
from turnvector_benchmark.cross_engine.metrics import (
    observation_from_stream_result,
    reduce_request_metrics,
)
from turnvector_benchmark.cross_engine.openai import OpenAIHTTPClient, build_chat_request


class ControlledClock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


def _request():
    return build_chat_request(
        model=FIXTURE_MODEL,
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ],
        output_tokens=3,
        include_usage=True,
        seed=7,
    )


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


class CrossEngineOpenAIE2ETests(unittest.TestCase):
    def test_fragmented_http_stream_metrics_and_artifact_custody(self) -> None:
        clock = ControlledClock(
            (
                1_000_000_000,
                1_100_000_000,
                1_200_000_000,
                1_300_000_000,
                1_500_000_000,
                1_800_000_000,
                1_900_000_000,
                2_000_000_000,
            )
        )
        with FixtureOpenAIServer("fragmented") as server:
            result = OpenAIHTTPClient(server.endpoint(), clock=clock).complete(_request())

        completion = result.completion
        self.assertEqual(completion.visible_text, "hello 世界")
        self.assertEqual(completion.reasoning_text, "private")
        self.assertEqual(completion.finish_reason, "stop")
        self.assertEqual(
            completion.usage.as_dict(),
            {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
        )
        self.assertEqual(completion.content_event_ns, (1_300_000_000, 1_500_000_000))
        self.assertEqual(completion.terminal_ns, 1_800_000_000)
        self.assertEqual(completion.raw_events[-1].data, "[DONE]")

        observation = observation_from_stream_result(
            "request-1",
            result,
            token_authority="server_usage",
            required_output_tokens=3,
        )
        metrics = reduce_request_metrics(observation)
        self.assertEqual(metrics.canonical_input_tokens, 4)
        self.assertEqual(metrics.canonical_output_tokens, 3)
        self.assertEqual(metrics.ttft_ms, 300.0)
        self.assertEqual(metrics.e2e_ms, 800.0)
        self.assertEqual(metrics.stream_event_interval_ms, (200.0,))
        self.assertEqual(metrics.client_post_first_output_ms_per_token, 100.0)
        self.assertAlmostEqual(metrics.effective_prefill_tokens_per_second, 40.0 / 3.0)
        self.assertTrue(metrics.completion_valid)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_bytes = {
                "request.json": result.request_bytes + b"\n",
                "stream_events.jsonl": b"".join(
                    _json_bytes(event.as_dict()) for event in completion.raw_events
                ),
                "request_metrics.json": _json_bytes(metrics.as_dict()),
            }
            for relative_path, content in artifact_bytes.items():
                (root / relative_path).write_bytes(content)
            specs = (
                ArtifactSpec(
                    "request",
                    "request.json",
                    "application/json",
                    None,
                    custody="benchmark_measurement",
                ),
                ArtifactSpec(
                    "stream-events",
                    "stream_events.jsonl",
                    "application/jsonl",
                    None,
                    custody="benchmark_measurement",
                ),
                ArtifactSpec(
                    "request-metrics",
                    "request_metrics.json",
                    "application/json",
                    None,
                    custody="benchmark_measurement",
                ),
            )
            manifest = build_artifact_manifest(root, specs, campaign_id="campaign-v1")
            write_artifact_manifest(root, manifest)
            write_sha256s_from_manifest(root, manifest)
            loaded = load_artifact_manifest(root)

            self.assertEqual(loaded, manifest)
            self.assertEqual(
                [entry["custody"] for entry in loaded["artifacts"]],
                ["benchmark_measurement"] * 3,
            )
            self.assertEqual(
                [entry["path"] for entry in loaded["artifacts"]],
                list(artifact_bytes),
            )
            for entry in loaded["artifacts"]:
                content = artifact_bytes[entry["path"]]
                self.assertEqual(entry["size_bytes"], len(content))
                self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(
                (root / "SHA256SUMS").read_bytes(),
                b"".join(
                    (
                        hashlib.sha256(artifact_bytes[path]).hexdigest()
                        + "  "
                        + path
                        + "\n"
                    ).encode("ascii")
                    for path in sorted(artifact_bytes)
                ),
            )

    def test_openai_path_has_no_qualification_or_data_plane_dependency(self) -> None:
        package_root = Path(__file__).parents[1] / "turnvector_benchmark" / "cross_engine"
        source = "\n".join(
            path.read_text(encoding="utf-8").lower() for path in sorted(package_root.glob("*.py"))
        )
        for forbidden in (
            "data plane",
            "data_plane",
            "turnvector.data-plane",
            "protobuf",
            "subjectadapter",
            "full_implementation_status",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
