from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, TextIO, Tuple

from .core import ContractError, canonical_json
from .lane_contract import SUBJECT_PROTOCOL, PlannedCase, SubjectAdapter


MAX_STDERR_BYTES = 65_536


def _strict_fields(
    value: Mapping[str, Any], required: Sequence[str], optional: Sequence[str], where: str
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError(f"{where} is missing required fields: {missing!r}")
    if unknown:
        raise ContractError(f"{where} has unknown fields: {unknown!r}")


def _nonempty_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be a non-empty string")
    return value


@dataclass(frozen=True)
class SubjectIdentity:
    name: str
    version: str
    kind: str
    build_identity: str


@dataclass(frozen=True)
class SubjectHello:
    identity: SubjectIdentity
    supported_lanes: Mapping[str, str]
    binary_manifest: Tuple[Mapping[str, str], ...]
    dependency_manifest: Tuple[Mapping[str, str], ...]
    environment_identity: Mapping[str, Any]
    data_plane: Optional[Mapping[str, Any]]


class SubjectSession:
    """A bounded, transcript-producing SubjectAdapter v1 session."""

    def __init__(self, adapter: SubjectAdapter) -> None:
        self.adapter = adapter
        self.process: Optional[subprocess.Popen[str]] = None
        self.responses: "queue.Queue[Optional[str]]" = queue.Queue()
        self.stderr_lines: List[str] = []
        self.transcript: List[Dict[str, Any]] = []
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    def __enter__(self) -> "SubjectSession":
        try:
            self.process = subprocess.Popen(
                list(self.adapter.command),
                cwd=str(self.adapter.cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise ContractError(
                f"cannot start subject adapter {self.adapter.command!r}: {error}"
            ) from error
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, args=(self.process.stdout,), daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, args=(self.process.stderr,), daemon=True
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
            if total >= MAX_STDERR_BYTES:
                continue
            captured = line[: MAX_STDERR_BYTES - total]
            self.stderr_lines.append(captured)
            total += len(captured)

    def stderr_text(self) -> str:
        return "".join(self.stderr_lines).strip()

    def request(self, message: Mapping[str, Any]) -> Dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise ContractError("subject adapter is not running")
        if self.process.poll() is not None:
            raise ContractError(
                f"subject adapter exited with {self.process.returncode}; "
                f"stderr={self.stderr_text()!r}"
            )
        request_value = dict(message)
        self.transcript.append({"direction": "request", "message": request_value})
        try:
            self.process.stdin.write(canonical_json(request_value) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ContractError(
                f"subject adapter closed stdin while handling {message.get('kind')!r}; "
                f"stderr={self.stderr_text()!r}"
            ) from error
        try:
            line = self.responses.get(timeout=self.adapter.timeout_seconds)
        except queue.Empty as error:
            raise ContractError(
                f"subject adapter timed out after {self.adapter.timeout_seconds:g}s while "
                f"handling {message.get('kind')!r}; stderr={self.stderr_text()!r}"
            ) from error
        if line is None:
            returncode = self.process.poll()
            raise ContractError(
                f"subject adapter closed stdout with exit code {returncode}; "
                f"stderr={self.stderr_text()!r}"
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"subject adapter emitted invalid JSON: {line.rstrip()!r}") from error
        if not isinstance(response, dict):
            raise ContractError("subject adapter response must be a JSON object")
        self.transcript.append({"direction": "response", "message": response})
        return response

    def hello(self, run_id: str, lane_id: str, lane_protocol: str) -> SubjectHello:
        response = self.request(
            {
                "kind": "hello",
                "protocol_version": SUBJECT_PROTOCOL,
                "run_id": run_id,
                "lane_id": lane_id,
                "lane_protocol": lane_protocol,
            }
        )
        _strict_fields(
            response,
            [
                "kind",
                "protocol_version",
                "subject",
                "supported_lanes",
                "binary_manifest",
                "dependency_manifest",
                "environment_identity",
            ],
            ["data_plane"],
            "hello_ack",
        )
        if response["kind"] != "hello_ack" or response["protocol_version"] != SUBJECT_PROTOCOL:
            raise ContractError(f"subject rejected protocol {SUBJECT_PROTOCOL!r}")
        subject = response["subject"]
        if not isinstance(subject, dict):
            raise ContractError("hello_ack.subject must be an object")
        _strict_fields(
            subject, ["name", "version", "kind", "build_identity"], [], "hello_ack.subject"
        )
        kind = _nonempty_string(subject["kind"], "hello_ack.subject.kind")
        if kind not in {"implementation", "fixture"}:
            raise ContractError("hello_ack.subject.kind must be implementation or fixture")
        supported = response["supported_lanes"]
        if not isinstance(supported, dict):
            raise ContractError("hello_ack.supported_lanes must be an object")
        parsed_supported: Dict[str, str] = {}
        for supported_lane, protocol in supported.items():
            parsed_supported[_nonempty_string(supported_lane, "supported lane ID")] = _nonempty_string(
                protocol, f"hello_ack.supported_lanes.{supported_lane}"
            )
        binaries = self._parse_binary_manifest(response["binary_manifest"])
        dependencies = self._parse_dependency_manifest(response["dependency_manifest"])
        environment = response["environment_identity"]
        if not isinstance(environment, dict) or not environment:
            raise ContractError("hello_ack.environment_identity must be a non-empty object")
        data_plane = response.get("data_plane")
        if data_plane is not None and (
            not isinstance(data_plane, dict) or not data_plane
        ):
            raise ContractError("hello_ack.data_plane must be a non-empty object")
        return SubjectHello(
            identity=SubjectIdentity(
                name=_nonempty_string(subject["name"], "hello_ack.subject.name"),
                version=_nonempty_string(subject["version"], "hello_ack.subject.version"),
                kind=kind,
                build_identity=_nonempty_string(
                    subject["build_identity"], "hello_ack.subject.build_identity"
                ),
            ),
            supported_lanes=parsed_supported,
            binary_manifest=binaries,
            dependency_manifest=dependencies,
            environment_identity=dict(environment),
            data_plane=None if data_plane is None else dict(data_plane),
        )

    @staticmethod
    def _parse_binary_manifest(value: Any) -> Tuple[Mapping[str, str], ...]:
        if not isinstance(value, list) or not value:
            raise ContractError("hello_ack.binary_manifest must be a non-empty array")
        result: List[Mapping[str, str]] = []
        for index, item in enumerate(value):
            where = f"hello_ack.binary_manifest[{index}]"
            if not isinstance(item, dict):
                raise ContractError(f"{where} must be an object")
            _strict_fields(item, ["path", "sha256"], [], where)
            digest = _nonempty_string(item["sha256"], f"{where}.sha256")
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ContractError(f"{where}.sha256 must be a lowercase SHA256")
            result.append(
                {
                    "path": _nonempty_string(item["path"], f"{where}.path"),
                    "sha256": digest,
                }
            )
        identities = [(item["path"], item["sha256"]) for item in result]
        if len(identities) != len(set(identities)):
            raise ContractError("hello_ack.binary_manifest must not contain duplicates")
        return tuple(result)

    @staticmethod
    def _parse_dependency_manifest(value: Any) -> Tuple[Mapping[str, str], ...]:
        if not isinstance(value, list):
            raise ContractError("hello_ack.dependency_manifest must be an array")
        result: List[Mapping[str, str]] = []
        for index, item in enumerate(value):
            where = f"hello_ack.dependency_manifest[{index}]"
            if not isinstance(item, dict):
                raise ContractError(f"{where} must be an object")
            _strict_fields(item, ["name", "version", "identity"], [], where)
            result.append(
                {
                    key: _nonempty_string(item[key], f"{where}.{key}")
                    for key in ("name", "version", "identity")
                }
            )
        names = [item["name"] for item in result]
        if len(names) != len(set(names)):
            raise ContractError("hello_ack.dependency_manifest names must be unique")
        return tuple(result)

    def case_open(
        self, run_id: str, case: PlannedCase, artifact_root: Path, case_directory: str
    ) -> str:
        response = self.request(
            {
                "kind": "case_open",
                "run_id": run_id,
                "case": case.as_dict(),
                "artifact_root": str(artifact_root),
                "case_directory": case_directory,
            }
        )
        _strict_fields(response, ["kind", "case_id", "status"], [], "case_open_ack")
        if response["kind"] != "case_open_ack" or response["case_id"] != case.case_id:
            raise ContractError(f"invalid case_open response for {case.case_id!r}")
        status = _nonempty_string(response["status"], "case_open_ack.status")
        if status not in {"ready", "unsupported", "environment_unavailable"}:
            raise ContractError("case_open_ack.status is not recognized")
        return status

    def case_step(
        self, case_id: str, step_index: int, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        response = self.request(
            {
                "kind": "case_step",
                "case_id": case_id,
                "step_index": step_index,
                "operation": operation,
                "payload": dict(payload),
            }
        )
        _strict_fields(
            response, ["kind", "case_id", "step_index", "evidence"], [], "case_step_ack"
        )
        if (
            response["kind"] != "case_step_ack"
            or response["case_id"] != case_id
            or response["step_index"] != step_index
        ):
            raise ContractError(f"invalid case_step response for {case_id!r} step {step_index}")
        evidence = response["evidence"]
        if not isinstance(evidence, dict):
            raise ContractError("case_step_ack.evidence must be an object")
        return evidence

    def case_close(self, case_id: str) -> Tuple[Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
        response = self.request({"kind": "case_close", "case_id": case_id})
        _strict_fields(
            response,
            ["kind", "case_id", "observations", "artifacts"],
            [],
            "case_close_ack",
        )
        if response["kind"] != "case_close_ack" or response["case_id"] != case_id:
            raise ContractError(f"invalid case_close response for {case_id!r}")
        observations = response["observations"]
        artifacts = response["artifacts"]
        if not isinstance(observations, dict):
            raise ContractError("case_close_ack.observations must be an object")
        if not isinstance(artifacts, list):
            raise ContractError("case_close_ack.artifacts must be an array")
        parsed_artifacts: List[Mapping[str, Any]] = []
        for index, artifact in enumerate(artifacts):
            where = f"case_close_ack.artifacts[{index}]"
            if not isinstance(artifact, dict):
                raise ContractError(f"{where} must be an object")
            _strict_fields(artifact, ["id", "path", "size", "sha256"], [], where)
            parsed_artifacts.append(dict(artifact))
        artifact_ids = [item["id"] for item in parsed_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ContractError("case_close_ack.artifact IDs must be unique within a case")
        return dict(observations), tuple(parsed_artifacts)

    def finish(self) -> None:
        response = self.request({"kind": "shutdown"})
        if response != {"kind": "shutdown_ack"}:
            raise ContractError(f"invalid shutdown response: {response!r}")
        assert self.process is not None
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            returncode = self.process.wait(timeout=self.adapter.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            raise ContractError("subject adapter did not exit after shutdown") from error
        if returncode != 0:
            raise ContractError(
                f"subject adapter exited with {returncode}; stderr={self.stderr_text()!r}"
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
