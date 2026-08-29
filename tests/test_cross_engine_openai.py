from __future__ import annotations

import copy
import unittest

from tests.fixtures.cross_engine.openai_server import (
    FIXTURE_MODEL,
    FixtureOpenAIServer,
    stream_body,
)
from turnvector_benchmark.core import ContractError
from turnvector_benchmark.cross_engine.openai import (
    OpenAIHTTPClient,
    OpenAIHTTPError,
    OpenAIProtocolError,
    SSEParser,
    build_chat_request,
    parse_endpoint_descriptor,
    validate_chat_request,
)


class ControlledClock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def request(include_usage=True):
    return build_chat_request(
        model=FIXTURE_MODEL,
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ],
        output_tokens=3,
        include_usage=include_usage,
        seed=7,
    )


class EndpointAndRequestContractTests(unittest.TestCase):
    def endpoint(self):
        with FixtureOpenAIServer() as server:
            return server.endpoint()

    def test_literal_loopback_endpoint_and_closed_request(self) -> None:
        parsed = parse_endpoint_descriptor(self.endpoint())
        self.assertEqual(parsed.request_path, "/v1/chat/completions")
        self.assertEqual(parsed.model_ids, (FIXTURE_MODEL,))
        value = request()
        self.assertEqual(value["stream_options"], {"include_usage": True})
        self.assertEqual(value["temperature"], 0)
        self.assertEqual(value["top_p"], 1)
        self.assertEqual(value["n"], 1)

    def test_endpoint_path_and_network_attacks_fail_closed(self) -> None:
        base = self.endpoint()
        attacks = (
            "http://localhost:31418/v1",
            "http://192.168.1.1:31418/v1",
            "http://127.0.0.1:31418/v1/../v1",
            "http://user@127.0.0.1:31418/v1",
            "http://127.0.0.1:31418/v1#fragment",
            "http://127.0.0.1:31418/%76%31",
            "https://127.0.0.1:31418/v1",
        )
        for url in attacks:
            with self.subTest(url=url):
                value = dict(base)
                value["base_url"] = url
                with self.assertRaises(ContractError):
                    parse_endpoint_descriptor(value)
        value = dict(base)
        value["data_plane"] = "turnvector.data-plane"
        with self.assertRaisesRegex(ContractError, "unknown fields"):
            parse_endpoint_descriptor(value)

    def test_request_unknown_fields_choices_and_output_bound_fail_closed(self) -> None:
        cases = []
        value = request()
        value["tools"] = []
        cases.append(value)
        value = request()
        value["n"] = 2
        cases.append(value)
        value = request()
        value["max_completion_tokens"] = 3
        cases.append(value)
        value = request()
        value["messages"][0]["role"] = "assistant"
        cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    validate_chat_request(value)


class SSEParserTests(unittest.TestCase):
    def test_arbitrary_utf8_fragmentation_preserves_channels_and_event_times(self) -> None:
        body = stream_body("normal")
        parser = SSEParser(clock=ControlledClock([10, 20, 30, 40, 50, 60, 70]))
        for byte in body:
            parser.feed(bytes([byte]))
        result = parser.finish()
        self.assertEqual(result.visible_output, "hello 世界".encode("utf-8"))
        self.assertEqual(result.reasoning_output, b"private")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage.as_dict(), {
            "prompt_tokens": 4,
            "completion_tokens": 3,
            "total_tokens": 7,
        })
        self.assertEqual(result.content_event_ns, (30, 40))
        self.assertEqual(result.terminal_ns, 50)
        self.assertEqual(len(result.raw_events), 7)
        self.assertEqual(result.raw_events[-1].data, "[DONE]")

    def test_malformed_partial_terminal_and_ordering_cases_fail_closed(self) -> None:
        modes = (
            "missing-terminal",
            "missing-done",
            "missing-usage",
            "duplicate-terminal",
            "content-after-terminal",
            "duplicate-done",
            "unknown-sse-field",
            "malformed-json",
            "multiple-choices",
            "index-drift",
            "invalid-utf8",
            "trailing-data",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                parser = SSEParser(clock=iter(range(100)).__next__)
                with self.assertRaises(ContractError):
                    parser.feed(stream_body(mode))
                    parser.finish()

    def test_response_and_event_byte_bounds_fail_closed(self) -> None:
        body = stream_body("normal")
        parser = SSEParser(max_response_bytes=len(body) - 1)
        with self.assertRaisesRegex(OpenAIProtocolError, "response exceeds"):
            parser.feed(body)
        parser = SSEParser(max_event_bytes=5)
        with self.assertRaisesRegex(OpenAIProtocolError, "event exceeds"):
            parser.feed(body)


class OpenAIHTTPClientTests(unittest.TestCase):
    def test_stdlib_client_owns_exact_post_and_parses_fragmented_stream(self) -> None:
        with FixtureOpenAIServer("fragmented") as server:
            clock = ControlledClock([100, 110, 120, 130, 140, 150, 160, 170])
            result = OpenAIHTTPClient(server.endpoint(), clock=clock).complete(request())
            self.assertEqual(result.dispatch_ns, 100)
            self.assertEqual(result.completion.visible_text, "hello 世界")
            self.assertEqual(result.completion.content_event_ns, (130, 140))
            self.assertEqual(result.completion.terminal_ns, 150)
            observed = server.requests
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]["path"], "/v1/chat/completions")
            self.assertEqual(observed[0]["accept"], "text/event-stream")
            self.assertEqual(observed[0]["accept_encoding"], "identity")
            self.assertEqual(observed[0]["body"], request())

    def test_redirect_non200_wrong_content_compression_and_oversize_fail_closed(self) -> None:
        for mode, exception in (
            ("redirect", OpenAIHTTPError),
            ("http-error", OpenAIHTTPError),
            ("wrong-content-type", OpenAIProtocolError),
            ("compressed", OpenAIProtocolError),
            ("oversized-error", OpenAIProtocolError),
        ):
            with self.subTest(mode=mode):
                with FixtureOpenAIServer(mode) as server:
                    client = OpenAIHTTPClient(server.endpoint())
                    with self.assertRaises(exception):
                        client.complete(request())
                    self.assertEqual(len(server.requests), 1, "client must never retry")

    def test_http_error_projection_is_bounded_and_contains_no_body(self) -> None:
        with FixtureOpenAIServer("http-error") as server:
            with self.assertRaises(OpenAIHTTPError) as caught:
                OpenAIHTTPClient(server.endpoint()).complete(request())
        projection = caught.exception.projection.as_dict()
        self.assertEqual(projection["status"], 503)
        self.assertEqual(
            set(projection),
            {"status", "reason", "content_type", "body_bytes", "body_sha256"},
        )
        self.assertNotIn("fixture", repr(projection))

    def test_authentication_value_is_used_but_never_retained_in_result(self) -> None:
        with FixtureOpenAIServer() as server:
            client = OpenAIHTTPClient(
                server.endpoint(authentication_env_var="FIXTURE_TOKEN"),
                environment={"FIXTURE_TOKEN": "super-secret"},
            )
            result = client.complete(request())
            self.assertTrue(server.requests[0]["authorization_present"])
            self.assertNotIn(b"super-secret", result.request_bytes)
            self.assertNotIn("super-secret", repr(result))

    def test_missing_required_authentication_fails_before_network(self) -> None:
        with FixtureOpenAIServer() as server:
            with self.assertRaisesRegex(ContractError, "unavailable"):
                OpenAIHTTPClient(
                    server.endpoint(authentication_env_var="FIXTURE_TOKEN"),
                    environment={},
                )
            self.assertEqual(server.requests, [])


if __name__ == "__main__":
    unittest.main()
