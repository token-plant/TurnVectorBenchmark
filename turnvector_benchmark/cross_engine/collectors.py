from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import ContractError
from ..evidence import sha256_file


MAX_PROCESS_IDS = 256
MAX_SAMPLES = 100000
MAX_ERRORS = 64


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    parent_pid: int
    process_group_id: int
    rss_bytes: int
    executable: str
    executable_sha256: Optional[str]
    start_marker: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "parent_pid": self.parent_pid,
            "process_group_id": self.process_group_id,
            "rss_bytes": self.rss_bytes,
            "executable": self.executable,
            "executable_sha256": self.executable_sha256,
            "start_marker": self.start_marker,
        }


@dataclass(frozen=True)
class ListenerRecord:
    host: str
    port: int
    owner_pid: int


@dataclass(frozen=True)
class CollectionResult:
    host_before: Mapping[str, Any]
    host_after: Mapping[str, Any]
    samples: Tuple[Mapping[str, Any], ...]
    errors: Tuple[str, ...]
    discarded_error_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "host_before": dict(self.host_before),
            "host_after": dict(self.host_after),
            "samples": [dict(sample) for sample in self.samples],
            "errors": list(self.errors),
            "discarded_error_count": self.discarded_error_count,
        }


def _positive_pid(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{where} must be a positive process ID")
    return value


def host_snapshot(clock: Callable[[], int] = time.monotonic_ns) -> Mapping[str, Any]:
    try:
        load = tuple(float(item) for item in os.getloadavg())
    except (AttributeError, OSError):
        load = ()
    return {
        "monotonic_ns": clock(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "load_average": list(load),
    }


def _executable_identity(command: str) -> Tuple[str, Optional[str]]:
    path = Path(command)
    if path.is_absolute() and path.is_file() and not path.is_symlink():
        return str(path), sha256_file(path)
    return command, None


def probe_process(pid: int) -> ProcessRecord:
    """Capture one bounded, Benchmark-clock process identity using fixed `ps`."""
    _positive_pid(pid, "pid")
    try:
        result = subprocess.run(
            [
                "ps",
                "-o",
                "pid=,ppid=,pgid=,rss=,lstart=,comm=",
                "-p",
                str(pid),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"cannot inspect pid {pid}: {error}") from error
    line = result.stdout.strip()
    fields = line.split(None, 9)
    if len(fields) != 10:
        raise RuntimeError(f"cannot parse process identity for pid {pid}")
    observed_pid, parent, group, rss = (int(fields[index]) for index in range(4))
    if observed_pid != pid:
        raise RuntimeError(f"process identity changed while inspecting pid {pid}")
    start_marker = " ".join(fields[4:9])
    executable, digest = _executable_identity(fields[9])
    return ProcessRecord(
        pid=pid,
        parent_pid=parent,
        process_group_id=group,
        rss_bytes=rss * 1024,
        executable=executable,
        executable_sha256=digest,
        start_marker=start_marker,
    )


def validate_process_ownership(
    records: Sequence[ProcessRecord],
    *,
    process_group_leader: int,
    expected_process_ids: Sequence[int],
    listener: Optional[ListenerRecord] = None,
) -> None:
    """Fail closed unless returned PIDs and listener belong to one owned group."""
    leader = _positive_pid(process_group_leader, "process_group_leader")
    expected = tuple(_positive_pid(pid, "expected_process_ids[]") for pid in expected_process_ids)
    if not expected or len(expected) != len(set(expected)):
        raise ContractError("expected_process_ids must be non-empty and unique")
    by_pid = {record.pid: record for record in records}
    if len(by_pid) != len(records):
        raise ContractError("process records must have unique PIDs")
    if set(by_pid) != set(expected):
        raise ContractError("process evidence does not exactly cover returned process IDs")
    if leader not in by_pid:
        raise ContractError("process group leader is absent from process evidence")
    for record in records:
        if record.process_group_id != leader:
            raise ContractError("returned process does not belong to the adapter-owned group")
    if listener is not None:
        if listener.host not in {"127.0.0.1", "::1"}:
            raise ContractError("listener must use a literal loopback address")
        if (
            isinstance(listener.port, bool)
            or not isinstance(listener.port, int)
            or not 1 <= listener.port <= 65535
        ):
            raise ContractError("listener port is invalid")
        if listener.owner_pid not in by_pid:
            raise ContractError("listener owner is not one of the returned process IDs")


def identities_stable(
    before: Sequence[ProcessRecord], after: Sequence[ProcessRecord]
) -> bool:
    before_by_pid = {record.pid: record for record in before}
    after_by_pid = {record.pid: record for record in after}
    if set(before_by_pid) != set(after_by_pid):
        return False
    return all(
        (
            before_by_pid[pid].start_marker,
            before_by_pid[pid].executable,
            before_by_pid[pid].executable_sha256,
            before_by_pid[pid].process_group_id,
        )
        == (
            after_by_pid[pid].start_marker,
            after_by_pid[pid].executable,
            after_by_pid[pid].executable_sha256,
            after_by_pid[pid].process_group_id,
        )
        for pid in before_by_pid
    )


class HostProcessCollector:
    """Bounded periodic host/process evidence independent of engine summaries."""

    def __init__(
        self,
        process_ids: Sequence[int],
        *,
        interval_seconds: float = 0.05,
        process_probe: Callable[[int], ProcessRecord] = probe_process,
        host_probe: Callable[[], Mapping[str, Any]] = host_snapshot,
        clock: Callable[[], int] = time.monotonic_ns,
        max_samples: int = MAX_SAMPLES,
    ) -> None:
        parsed = tuple(_positive_pid(pid, "process_ids[]") for pid in process_ids)
        if not parsed or len(parsed) > MAX_PROCESS_IDS or len(parsed) != len(set(parsed)):
            raise ContractError("process_ids must be a bounded non-empty unique array")
        if not isinstance(interval_seconds, (int, float)) or isinstance(interval_seconds, bool) or interval_seconds <= 0:
            raise ContractError("interval_seconds must be positive")
        if isinstance(max_samples, bool) or not isinstance(max_samples, int) or not 1 <= max_samples <= MAX_SAMPLES:
            raise ContractError(f"max_samples must be in [1, {MAX_SAMPLES}]")
        self.process_ids = parsed
        self.interval_seconds = float(interval_seconds)
        self.process_probe = process_probe
        self.host_probe = host_probe
        self.clock = clock
        self.max_samples = max_samples
        self.samples: List[Mapping[str, Any]] = []
        self._errors: Dict[str, int] = {}
        self._discarded_errors = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._host_before: Mapping[str, Any] = {}

    def _record_error(self, message: str) -> None:
        if message in self._errors:
            self._errors[message] += 1
        elif len(self._errors) < MAX_ERRORS:
            self._errors[message] = 1
        else:
            self._discarded_errors += 1

    def sample(self) -> Mapping[str, Any]:
        records: List[Mapping[str, Any]] = []
        for pid in self.process_ids:
            try:
                records.append(self.process_probe(pid).as_dict())
            except Exception as error:
                self._record_error(f"pid {pid}: {error}")
        sample = {
            "monotonic_ns": self.clock(),
            "processes": records,
            "total_rss_bytes": sum(int(record["rss_bytes"]) for record in records),
        }
        if len(self.samples) < self.max_samples:
            self.samples.append(sample)
        else:
            self._record_error("sample bound reached")
        return sample

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("collector already started")
        self._host_before = dict(self.host_probe())
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> CollectionResult:
        if self._thread is None:
            raise RuntimeError("collector was not started")
        self._stop.set()
        self._thread.join(timeout=max(5.0, self.interval_seconds * 2))
        if self._thread.is_alive():
            raise RuntimeError("collector did not stop")
        errors = tuple(
            message if count == 1 else f"{message} (repeated {count} times)"
            for message, count in self._errors.items()
        )
        return CollectionResult(
            host_before=self._host_before,
            host_after=dict(self.host_probe()),
            samples=tuple(self.samples),
            errors=errors,
            discarded_error_count=self._discarded_errors,
        )


__all__ = [
    "CollectionResult",
    "HostProcessCollector",
    "ListenerRecord",
    "ProcessRecord",
    "host_snapshot",
    "identities_stable",
    "probe_process",
    "validate_process_ownership",
]
