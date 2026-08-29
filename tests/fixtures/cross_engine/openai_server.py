"""Synthetic loopback OpenAI HTTP/SSE target for ordinary CI."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Mapping, Optional, Sequence


FIXTURE_MODEL = "fixture-model"


def _chunk(
    *,
    delta: Optional[Mapping[str, Any]] = None,
    finish_reason: Optional[str] = None,
    index: int = 0,
    choices: Optional[Sequence[Mapping[str, Any]]] = None,
    usage: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    if choices is None:
        choices = [
            {
                "index": index,
                "delta": dict(delta or {}),
                "finish_reason": finish_reason,
            }
        ]
    result: Dict[str, Any] = {
        "id": "fixture-request",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": FIXTURE_MODEL,
        "choices": list(choices),
    }
    if usage is not None:
        result["usage"] = dict(usage)
    return result


def _event(value: Any) -> bytes:
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    return b"data: " + data + b"\n\n"


def stream_body(mode: str) -> bytes:
    role = _event(_chunk(delta={"role": "assistant"}))
    reasoning = _event(_chunk(delta={"reasoning_content": "private"}))
    first = _event(_chunk(delta={"content": "hello "}))
    second = _event(_chunk(delta={"content": "世界"}))
    terminal = _event(_chunk(delta={}, finish_reason="stop"))
    usage = _event(
        _chunk(
            choices=[],
            usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
        )
    )
    done = _event("[DONE]")
    if mode in ("normal", "fragmented"):
        return role + reasoning + first + second + terminal + usage + done
    if mode == "missing-terminal":
        return role + first + done
    if mode == "missing-done":
        return role + first + terminal + usage
    if mode == "missing-usage":
        return role + first + terminal + done
    if mode == "duplicate-terminal":
        return role + first + terminal + terminal + usage + done
    if mode == "content-after-terminal":
        return role + first + terminal + second + usage + done
    if mode == "duplicate-done":
        return role + first + terminal + usage + done + done
    if mode == "unknown-sse-field":
        return b"event: message\n" + role + terminal + usage + done
    if mode == "malformed-json":
        return _event("{not-json}")
    if mode == "multiple-choices":
        choice = {"index": 0, "delta": {"content": "x"}, "finish_reason": None}
        return _event(_chunk(choices=[choice, choice]))
    if mode == "index-drift":
        return _event(_chunk(index=1, delta={"content": "x"}))
    if mode == "invalid-utf8":
        return b"data: \xff\n\n"
    if mode == "trailing-data":
        return role + first + terminal + usage + done + b"data: {}\n\n"
    if mode == "truncated-output":
        return (
            role
            + _event(_chunk(delta={"content": "x"}))
            + terminal
            + _event(
                _chunk(
                    choices=[],
                    usage={"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                )
            )
            + done
        )
    if mode == "fully-buffered":
        return (
            role
            + _event(_chunk(delta={"content": "hello world"}))
            + terminal
            + _event(
                _chunk(
                    choices=[],
                    usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                )
            )
            + done
        )
    raise ValueError(mode)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, mode: str) -> None:
        super().__init__(address, handler)
        self.mode = mode
        self.requests: List[Dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text or "")
        except ValueError:
            self.send_error(400)
            return
        body = self.rfile.read(length)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = None
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "body": value,
                "accept": self.headers.get("Accept"),
                "accept_encoding": self.headers.get("Accept-Encoding"),
                "content_type": self.headers.get("Content-Type"),
                "authorization_present": self.headers.get("Authorization") is not None,
            }
        )
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        mode = self.server.mode  # type: ignore[attr-defined]
        if mode == "redirect":
            self.send_response(307)
            self.send_header("Location", "/v1/chat/completions")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        if mode == "http-error":
            response = b'{"error":"fixture"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response)
            return
        if mode == "oversized-error":
            response = b"x" * (70 * 1024)
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response)
            return
        body_bytes = stream_body(mode if mode not in {"wrong-content-type", "compressed"} else "normal")
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json" if mode == "wrong-content-type" else "text/event-stream; charset=utf-8",
        )
        if mode == "compressed":
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        if mode == "fragmented":
            # Includes splits inside JSON syntax and the three-byte UTF-8 encoding.
            sizes = (1, 2, 5, 3, 7, 11)
            offset = 0
            ordinal = 0
            while offset < len(body_bytes):
                size = sizes[ordinal % len(sizes)]
                self.wfile.write(body_bytes[offset : offset + size])
                self.wfile.flush()
                offset += size
                ordinal += 1
        else:
            self.wfile.write(body_bytes)


class FixtureOpenAIServer:
    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode
        self._server = _Server(("127.0.0.1", 0), _Handler, mode)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def requests(self) -> List[Dict[str, Any]]:
        return list(self._server.requests)

    def endpoint(self, *, authentication_env_var: Optional[str] = None) -> Dict[str, Any]:
        return {
            "protocol_family": "openai-compatible",
            "protocol_version": "turnvector.benchmark.openai-serving.v1",
            "transport": "http",
            "base_url": f"http://127.0.0.1:{self.port}/v1",
            "api_flavor": "chat_completions",
            "stream_format": "sse-data-json",
            "model_ids": [FIXTURE_MODEL],
            "process_ids": [os.getpid()],
            "capability_report_sha256": "a" * 64,
            "authentication_env_var": authentication_env_var,
        }

    def start(self) -> "FixtureOpenAIServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> "FixtureOpenAIServer":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
