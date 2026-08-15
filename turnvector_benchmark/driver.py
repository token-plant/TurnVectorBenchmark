from __future__ import annotations

import json
import queue
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

from .core import ContractError, canonical_json


class DriverProcess:
    """One bounded JSONL session with a benchmark driver."""

    def __init__(
        self,
        command: str,
        cwd: Path,
        response_timeout_seconds: float,
    ) -> None:
        argv = shlex.split(command)
        if not argv:
            raise ContractError("driver command must not be empty")
        self.argv = argv
        self.cwd = cwd
        self.response_timeout_seconds = response_timeout_seconds
        self.process: Optional[subprocess.Popen[str]] = None
        self.responses: "queue.Queue[Optional[str]]" = queue.Queue()
        self.stderr_lines: List[str] = []
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    def __enter__(self) -> "DriverProcess":
        try:
            self.process = subprocess.Popen(
                self.argv,
                cwd=str(self.cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise ContractError(f"cannot start driver {self.argv!r}: {error}") from error
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self.process.stdout,),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(self.process.stderr,),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        return self

    def _read_stdout(self, stream: TextIO) -> None:
        for line in stream:
            self.responses.put(line)
        self.responses.put(None)

    def _read_stderr(self, stream: TextIO) -> None:
        total = 0
        for line in stream:
            if total >= 65_536:
                continue
            remaining = 65_536 - total
            captured = line[:remaining]
            self.stderr_lines.append(captured)
            total += len(captured)

    def stderr_text(self) -> str:
        return "".join(self.stderr_lines).strip()

    def request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise ContractError("driver process is not running")
        if self.process.poll() is not None:
            raise ContractError(
                f"driver exited with {self.process.returncode} before request; "
                f"stderr={self.stderr_text()!r}"
            )
        try:
            self.process.stdin.write(canonical_json(message) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ContractError(
                f"driver closed stdin while receiving {message.get('kind')!r}; "
                f"stderr={self.stderr_text()!r}"
            ) from error
        try:
            line = self.responses.get(timeout=self.response_timeout_seconds)
        except queue.Empty as error:
            raise ContractError(
                f"driver timed out after {self.response_timeout_seconds:g}s while handling "
                f"{message.get('kind')!r}; stderr={self.stderr_text()!r}"
            ) from error
        if line is None:
            returncode = self.process.poll()
            raise ContractError(
                f"driver closed stdout with exit code {returncode}; stderr={self.stderr_text()!r}"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"driver emitted invalid JSON: {line.rstrip()!r}") from error
        if not isinstance(value, dict):
            raise ContractError("driver response must be a JSON object")
        return value

    def finish(self) -> None:
        response = self.request({"kind": "shutdown"})
        if response != {"kind": "shutdown_ack"}:
            raise ContractError(f"invalid shutdown response: {response!r}")
        assert self.process is not None
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            returncode = self.process.wait(timeout=self.response_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            raise ContractError("driver did not exit after shutdown acknowledgement") from error
        if returncode != 0:
            raise ContractError(
                f"driver exited with {returncode} after shutdown; stderr={self.stderr_text()!r}"
            )

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=1.0)
