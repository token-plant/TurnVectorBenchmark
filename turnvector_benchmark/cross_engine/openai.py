from __future__ import annotations

import codecs
import hashlib
import http.client
import ipaddress
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from ..core import ContractError
from .contracts import (
    OPENAI_SERVING_PROTOCOL_VERSION,
    bounded_array,
    bounded_string,
    canonical_json_bytes,
    environment_name,
    identifier,
    integer,
    sha256_digest,
    strict_json_loads,
    strict_object,
    unique_strings,
)


OPENAI_ENDPOINT_FIELDS = (
    "protocol_family",
    "protocol_version",
    "transport",
    "base_url",
    "api_flavor",
    "stream_format",
    "model_ids",
    "process_ids",
    "capability_report_sha256",
    "authentication_env_var",
)
MAX_REQUEST_BYTES = 256 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_EVENT_BYTES = 256 * 1024
DEFAULT_MAX_ERROR_BYTES = 64 * 1024


class OpenAIProtocolError(ContractError):
    pass


@dataclass(frozen=True)
class OpenAIEndpoint:
    base_url: str
    host: str
    port: int
    model_ids: Tuple[str, ...]
    process_ids: Tuple[int, ...]
    capability_report_sha256: str
    authentication_env_var: Optional[str]

    @property
    def request_path(self) -> str:
        return "/v1/chat/completions"


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def as_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class RawSSEEvent:
    ordinal: int
    data: str
    received_ns: int
    size_bytes: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "data": self.data,
            "received_ns": self.received_ns,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ParsedChatCompletion:
    visible_output: bytes
    reasoning_output: bytes
    finish_reason: str
    usage: Optional[Usage]
    terminal_ns: int
    content_event_ns: Tuple[int, ...]
    raw_events: Tuple[RawSSEEvent, ...]
    response_bytes: int

    @property
    def visible_text(self) -> str:
        return self.visible_output.decode("utf-8")

    @property
    def reasoning_text(self) -> str:
        return self.reasoning_output.decode("utf-8")

    @property
    def visible_sha256(self) -> str:
        return hashlib.sha256(self.visible_output).hexdigest()


@dataclass(frozen=True)
class OpenAIStreamResult:
    request_bytes: bytes
    dispatch_ns: int
    completion: ParsedChatCompletion
    http_status: int
    content_type: str

    @property
    def request_sha256(self) -> str:
        return hashlib.sha256(self.request_bytes).hexdigest()


@dataclass(frozen=True)
class HTTPErrorProjection:
    status: int
    reason: str
    content_type: Optional[str]
    body_bytes: int
    body_sha256: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "content_type": self.content_type,
            "body_bytes": self.body_bytes,
            "body_sha256": self.body_sha256,
        }


class OpenAIHTTPError(ContractError):
    def __init__(self, projection: HTTPErrorProjection) -> None:
        super().__init__(f"OpenAI endpoint returned HTTP {projection.status}")
        self.projection = projection


def parse_endpoint_descriptor(value: Any, where: str = "endpoint") -> OpenAIEndpoint:
    obj = strict_object(value, OPENAI_ENDPOINT_FIELDS, where=where)
    expected_constants = {
        "protocol_family": "openai-compatible",
        "protocol_version": OPENAI_SERVING_PROTOCOL_VERSION,
        "transport": "http",
        "api_flavor": "chat_completions",
        "stream_format": "sse-data-json",
    }
    for field, expected in expected_constants.items():
        if obj[field] != expected:
            raise ContractError(f"{where}.{field} must equal {expected!r}")
    base_url = bounded_string(obj["base_url"], f"{where}.base_url", maximum_bytes=512)
    if any(character in base_url for character in ("%", "\\", "\r", "\n", "\t")):
        raise ContractError(f"{where}.base_url contains an ambiguous encoding")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as error:
        raise ContractError(f"{where}.base_url is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1"
        or parsed.hostname is None
        or port is None
    ):
        raise ContractError(
            f"{where}.base_url must be an exact literal-loopback http://HOST:PORT/v1 URL"
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ContractError(f"{where}.base_url host must be a literal IP address") from error
    if isinstance(address, ipaddress.IPv4Address):
        admitted = address in ipaddress.ip_network("127.0.0.0/8")
        expected_netloc = f"{address.compressed}:{port}"
    else:
        admitted = address == ipaddress.ip_address("::1")
        expected_netloc = f"[{address.compressed}]:{port}"
    if not admitted:
        raise ContractError(f"{where}.base_url host must be in 127.0.0.0/8 or exactly ::1")
    if parsed.netloc != expected_netloc:
        raise ContractError(f"{where}.base_url must use an unambiguous literal-loopback encoding")
    if port < 1 or port > 65535:
        raise ContractError(f"{where}.base_url port must be between 1 and 65535")
    model_ids = unique_strings(
        obj["model_ids"],
        f"{where}.model_ids",
        maximum_items=64,
        parse=identifier,
        allow_empty=False,
    )
    raw_pids = bounded_array(
        obj["process_ids"], f"{where}.process_ids", maximum_items=1024, allow_empty=False
    )
    process_ids = tuple(
        integer(item, f"{where}.process_ids[{index}]", minimum=1, maximum=2**31 - 1)
        for index, item in enumerate(raw_pids)
    )
    if len(process_ids) != len(set(process_ids)):
        raise ContractError(f"{where}.process_ids must not contain duplicates")
    auth = obj["authentication_env_var"]
    if auth is not None:
        auth = environment_name(auth, f"{where}.authentication_env_var")
    return OpenAIEndpoint(
        base_url=base_url,
        host=address.compressed,
        port=port,
        model_ids=model_ids,
        process_ids=process_ids,
        capability_report_sha256=sha256_digest(
            obj["capability_report_sha256"], f"{where}.capability_report_sha256"
        ),
        authentication_env_var=auth,
    )


def validate_chat_request(value: Any, where: str = "OpenAI request") -> Dict[str, Any]:
    obj = strict_object(
        value,
        ("model", "messages", "stream", "temperature", "top_p", "n"),
        (
            "stream_options",
            "seed",
            "max_tokens",
            "max_completion_tokens",
        ),
        where,
    )
    identifier(obj["model"], f"{where}.model")
    messages = bounded_array(
        obj["messages"], f"{where}.messages", maximum_items=128, allow_empty=False
    )
    for index, raw in enumerate(messages):
        message_where = f"{where}.messages[{index}]"
        message = strict_object(raw, ("role", "content"), where=message_where)
        if message["role"] not in ("system", "user"):
            raise ContractError(f"{message_where}.role must be 'system' or 'user'")
        bounded_string(
            message["content"], message_where + ".content", maximum_bytes=128 * 1024
        )
    if obj["stream"] is not True:
        raise ContractError(f"{where}.stream must be exactly true")
    if isinstance(obj["temperature"], bool) or obj["temperature"] != 0:
        raise ContractError(f"{where}.temperature must be exactly 0")
    if isinstance(obj["top_p"], bool) or obj["top_p"] != 1:
        raise ContractError(f"{where}.top_p must be exactly 1")
    if isinstance(obj["n"], bool) or obj["n"] != 1:
        raise ContractError(f"{where}.n must be exactly 1")
    output_fields = [field for field in ("max_tokens", "max_completion_tokens") if field in obj]
    if len(output_fields) != 1:
        raise ContractError(f"{where} must contain exactly one output-bound field")
    integer(obj[output_fields[0]], f"{where}.{output_fields[0]}", minimum=1)
    if "seed" in obj:
        integer(obj["seed"], f"{where}.seed", minimum=-(2**63), maximum=2**63 - 1)
    if "stream_options" in obj:
        options = strict_object(
            obj["stream_options"], ("include_usage",), where=f"{where}.stream_options"
        )
        if options["include_usage"] is not True:
            raise ContractError(f"{where}.stream_options.include_usage must be exactly true")
    if len(canonical_json_bytes(obj)) > MAX_REQUEST_BYTES:
        raise ContractError(f"{where} exceeds the {MAX_REQUEST_BYTES}-byte bound")
    return obj


def build_chat_request(
    *,
    model: str,
    messages: Sequence[Mapping[str, str]],
    output_tokens: int,
    output_bound_field: str = "max_tokens",
    include_usage: bool = True,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    if output_bound_field not in ("max_tokens", "max_completion_tokens"):
        raise ContractError("output_bound_field is not registered for dialect v1")
    value: Dict[str, Any] = {
        "model": model,
        "messages": [dict(item) for item in messages],
        "stream": True,
        "temperature": 0,
        "top_p": 1,
        "n": 1,
        output_bound_field: output_tokens,
    }
    if include_usage:
        value["stream_options"] = {"include_usage": True}
    if seed is not None:
        value["seed"] = seed
    return validate_chat_request(value)


class SSEParser:
    """Incremental strict parser for the Benchmark OpenAI SSE dialect."""

    def __init__(
        self,
        *,
        clock: Callable[[], int] = time.monotonic_ns,
        require_usage: bool = True,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
    ) -> None:
        integer(max_response_bytes, "max_response_bytes", minimum=1)
        integer(max_event_bytes, "max_event_bytes", minimum=1)
        self.clock = clock
        self.require_usage = require_usage
        self.max_response_bytes = max_response_bytes
        self.max_event_bytes = max_event_bytes
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._text = ""
        self._pending_data: Optional[str] = None
        self._response_bytes = 0
        self._events: List[RawSSEEvent] = []
        self._visible: List[bytes] = []
        self._reasoning: List[bytes] = []
        self._content_event_ns: List[int] = []
        self._terminal_ns: Optional[int] = None
        self._finish_reason: Optional[str] = None
        self._usage: Optional[Usage] = None
        self._done = False
        self._role_seen = False
        self._identity: Dict[str, Any] = {}
        self._last_timestamp = -1

    def feed(self, data: bytes) -> Tuple[RawSSEEvent, ...]:
        if not isinstance(data, bytes):
            raise OpenAIProtocolError("SSE input must be bytes")
        if not data:
            return ()
        if self._done:
            raise OpenAIProtocolError("SSE data appeared after [DONE]")
        self._response_bytes += len(data)
        if self._response_bytes > self.max_response_bytes:
            raise OpenAIProtocolError(
                f"SSE response exceeds the {self.max_response_bytes}-byte bound"
            )
        try:
            decoded = self._decoder.decode(data, final=False)
        except UnicodeDecodeError as error:
            raise OpenAIProtocolError("SSE response is not valid UTF-8") from error
        if "\r" in decoded:
            raise OpenAIProtocolError("SSE dialect requires LF line endings")
        self._text += decoded
        before = len(self._events)
        while "\n" in self._text:
            line, self._text = self._text.split("\n", 1)
            self._accept_line(line)
        return tuple(self._events[before:])

    def _accept_line(self, line: str) -> None:
        if self._done:
            raise OpenAIProtocolError("SSE data appeared after [DONE]")
        if line == "":
            if self._pending_data is None:
                raise OpenAIProtocolError("SSE contains an empty event separator")
            data = self._pending_data
            self._pending_data = None
            encoded = data.encode("utf-8")
            if len(encoded) > self.max_event_bytes:
                raise OpenAIProtocolError(
                    f"SSE event exceeds the {self.max_event_bytes}-byte bound"
                )
            timestamp = self.clock()
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
                raise OpenAIProtocolError("Benchmark clock must return non-negative integer nanoseconds")
            if timestamp < self._last_timestamp:
                raise OpenAIProtocolError("Benchmark clock moved backwards")
            self._last_timestamp = timestamp
            event = RawSSEEvent(len(self._events) + 1, data, timestamp, len(encoded))
            self._events.append(event)
            self._accept_event(event)
            return
        if not line.startswith("data:"):
            raise OpenAIProtocolError("SSE admits only data: fields and blank separators")
        if self._pending_data is not None:
            raise OpenAIProtocolError("SSE event must contain exactly one data: field")
        data = line[5:]
        if data.startswith(" "):
            data = data[1:]
        if not data:
            raise OpenAIProtocolError("SSE data field must not be empty")
        if len(data.encode("utf-8")) > self.max_event_bytes:
            raise OpenAIProtocolError(
                f"SSE event exceeds the {self.max_event_bytes}-byte bound"
            )
        self._pending_data = data

    def _accept_event(self, event: RawSSEEvent) -> None:
        if event.data == "[DONE]":
            if self._terminal_ns is None:
                raise OpenAIProtocolError("[DONE] appeared before a terminal chunk")
            if self.require_usage and self._usage is None:
                raise OpenAIProtocolError("[DONE] appeared before the required usage chunk")
            self._done = True
            return
        value = strict_json_loads(event.data.encode("utf-8"), "SSE data JSON")
        chunk = strict_object(
            value,
            ("choices",),
            ("id", "object", "created", "model", "system_fingerprint", "usage"),
            "SSE chunk",
        )
        self._validate_chunk_identity(chunk)
        choices = bounded_array(chunk["choices"], "SSE chunk.choices", maximum_items=1)
        usage_value = chunk.get("usage")
        if not choices:
            if self._terminal_ns is None:
                raise OpenAIProtocolError("usage chunk appeared before the terminal chunk")
            if self._usage is not None:
                raise OpenAIProtocolError("duplicate usage chunk")
            if usage_value is None:
                raise OpenAIProtocolError("empty choices are admitted only for a usage chunk")
            self._usage = self._parse_usage(usage_value)
            return
        if self._terminal_ns is not None:
            raise OpenAIProtocolError("content or terminal chunk appeared after terminal")
        if usage_value is not None:
            raise OpenAIProtocolError("usage is admitted only in the post-terminal usage chunk")
        choice = strict_object(
            choices[0], ("index", "delta", "finish_reason"), where="SSE choice"
        )
        if integer(choice["index"], "SSE choice.index", maximum=0) != 0:
            raise OpenAIProtocolError("SSE choice index drifted from zero")
        delta = strict_object(
            choice["delta"], (), ("role", "reasoning_content", "content"), "SSE choice.delta"
        )
        finish_reason = choice["finish_reason"]
        if finish_reason is not None:
            bounded_string(finish_reason, "SSE choice.finish_reason", maximum_bytes=128)
            if any(value not in (None, "") for value in delta.values()):
                raise OpenAIProtocolError("terminal chunk must not contain output content")
            self._finish_reason = finish_reason
            self._terminal_ns = event.received_ns
            return
        if "role" in delta:
            if delta["role"] != "assistant":
                raise OpenAIProtocolError("SSE delta role must be assistant")
            if self._role_seen or self._visible or self._reasoning:
                raise OpenAIProtocolError("SSE assistant role must appear at most once before output")
            self._role_seen = True
        for field, target in (
            ("reasoning_content", self._reasoning),
            ("content", self._visible),
        ):
            if field not in delta:
                continue
            text = delta[field]
            if text is None:
                continue
            bounded_string(text, f"SSE choice.delta.{field}", maximum_bytes=self.max_event_bytes, allow_empty=True)
            encoded = text.encode("utf-8")
            if encoded:
                target.append(encoded)
                if field == "content":
                    self._content_event_ns.append(event.received_ns)

    def _validate_chunk_identity(self, chunk: Mapping[str, Any]) -> None:
        if "object" in chunk and chunk["object"] != "chat.completion.chunk":
            raise OpenAIProtocolError("SSE chunk.object must be chat.completion.chunk")
        if "created" in chunk:
            integer(chunk["created"], "SSE chunk.created")
        for field in ("id", "model"):
            if field in chunk:
                bounded_string(chunk[field], f"SSE chunk.{field}", maximum_bytes=256)
                if field in self._identity and self._identity[field] != chunk[field]:
                    raise OpenAIProtocolError(f"SSE chunk {field} identity drifted")
                self._identity[field] = chunk[field]
        if "system_fingerprint" in chunk and chunk["system_fingerprint"] is not None:
            bounded_string(
                chunk["system_fingerprint"], "SSE chunk.system_fingerprint", maximum_bytes=256
            )
            if (
                "system_fingerprint" in self._identity
                and self._identity["system_fingerprint"] != chunk["system_fingerprint"]
            ):
                raise OpenAIProtocolError("SSE system_fingerprint identity drifted")
            self._identity["system_fingerprint"] = chunk["system_fingerprint"]

    @staticmethod
    def _parse_usage(value: Any) -> Usage:
        obj = strict_object(
            value,
            ("prompt_tokens", "completion_tokens", "total_tokens"),
            where="SSE usage",
        )
        prompt = integer(obj["prompt_tokens"], "SSE usage.prompt_tokens")
        completion = integer(obj["completion_tokens"], "SSE usage.completion_tokens")
        total = integer(obj["total_tokens"], "SSE usage.total_tokens")
        if prompt + completion != total:
            raise OpenAIProtocolError("SSE usage total_tokens does not reconcile")
        return Usage(prompt, completion, total)

    def finish(self) -> ParsedChatCompletion:
        try:
            remainder = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise OpenAIProtocolError("SSE response ended inside a UTF-8 sequence") from error
        self._text += remainder
        if self._text or self._pending_data is not None:
            raise OpenAIProtocolError("SSE response ended inside an event")
        if self._terminal_ns is None or self._finish_reason is None:
            raise OpenAIProtocolError("SSE response is missing a terminal chunk")
        if not self._done:
            raise OpenAIProtocolError("SSE response is missing [DONE]")
        if self.require_usage and self._usage is None:
            raise OpenAIProtocolError("SSE response is missing required usage")
        return ParsedChatCompletion(
            visible_output=b"".join(self._visible),
            reasoning_output=b"".join(self._reasoning),
            finish_reason=self._finish_reason,
            usage=self._usage,
            terminal_ns=self._terminal_ns,
            content_event_ns=tuple(self._content_event_ns),
            raw_events=tuple(self._events),
            response_bytes=self._response_bytes,
        )


def _content_type(value: Optional[str]) -> str:
    if value is None:
        raise OpenAIProtocolError("HTTP 200 response is missing Content-Type")
    parts = [item.strip() for item in value.split(";")]
    if parts[0].lower() != "text/event-stream":
        raise OpenAIProtocolError("HTTP 200 response Content-Type must be text/event-stream")
    for parameter in parts[1:]:
        if parameter.lower() not in ("charset=utf-8", 'charset="utf-8"'):
            raise OpenAIProtocolError("HTTP 200 response has an unsupported Content-Type parameter")
    return value


class OpenAIHTTPClient:
    """Benchmark-owned, no-proxy/no-redirect/no-retry OpenAI HTTP/1.1 client."""

    def __init__(
        self,
        endpoint: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        clock: Callable[[], int] = time.monotonic_ns,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        max_error_bytes: int = DEFAULT_MAX_ERROR_BYTES,
        environment: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.endpoint = parse_endpoint_descriptor(endpoint)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ContractError("timeout_seconds must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.clock = clock
        self.max_response_bytes = integer(max_response_bytes, "max_response_bytes", minimum=1)
        self.max_event_bytes = integer(max_event_bytes, "max_event_bytes", minimum=1)
        self.max_error_bytes = integer(max_error_bytes, "max_error_bytes", minimum=1)
        environment_values = os.environ if environment is None else environment
        self._authorization: Optional[str] = None
        if self.endpoint.authentication_env_var is not None:
            secret = environment_values.get(self.endpoint.authentication_env_var)
            if not secret:
                raise ContractError("endpoint authentication environment variable is unavailable")
            self._authorization = secret

    def complete(self, request: Mapping[str, Any]) -> OpenAIStreamResult:
        value = validate_chat_request(dict(request))
        if value["model"] not in self.endpoint.model_ids:
            raise ContractError("OpenAI request model is not declared by the endpoint")
        request_bytes = canonical_json_bytes(value)
        require_usage = "stream_options" in value
        headers = {
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(request_bytes)),
            "Connection": "close",
            "User-Agent": "TurnVectorBenchmark/cross-engine-v1",
        }
        if self._authorization is not None:
            headers["Authorization"] = f"Bearer {self._authorization}"
        connection = http.client.HTTPConnection(
            self.endpoint.host, self.endpoint.port, timeout=self.timeout_seconds
        )
        dispatch_ns = self.clock()
        try:
            connection.request(
                "POST",
                self.endpoint.request_path,
                body=request_bytes,
                headers=headers,
                encode_chunked=False,
            )
            response = connection.getresponse()
            if response.version != 11:
                raise OpenAIProtocolError("OpenAI endpoint must respond with HTTP/1.1")
            if response.status != 200:
                body = response.read(self.max_error_bytes + 1)
                if len(body) > self.max_error_bytes:
                    raise OpenAIProtocolError(
                        f"HTTP error body exceeds the {self.max_error_bytes}-byte bound"
                    )
                projection = HTTPErrorProjection(
                    status=response.status,
                    reason=bounded_string(
                        response.reason or "unknown", "HTTP reason", maximum_bytes=256
                    ),
                    content_type=response.getheader("Content-Type"),
                    body_bytes=len(body),
                    body_sha256=hashlib.sha256(body).hexdigest(),
                )
                raise OpenAIHTTPError(projection)
            content_type = _content_type(response.getheader("Content-Type"))
            encoding = response.getheader("Content-Encoding")
            if encoding not in (None, "", "identity"):
                raise OpenAIProtocolError("compressed OpenAI responses are forbidden")
            parser = SSEParser(
                clock=self.clock,
                require_usage=require_usage,
                max_response_bytes=self.max_response_bytes,
                max_event_bytes=self.max_event_bytes,
            )
            while True:
                chunk = response.read(16 * 1024)
                if not chunk:
                    break
                parser.feed(chunk)
            completion = parser.finish()
            return OpenAIStreamResult(
                request_bytes=request_bytes,
                dispatch_ns=dispatch_ns,
                completion=completion,
                http_status=response.status,
                content_type=content_type,
            )
        except OpenAIHTTPError:
            raise
        except OpenAIProtocolError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise OpenAIProtocolError(f"OpenAI HTTP transport failed: {error}") from error
        finally:
            connection.close()
