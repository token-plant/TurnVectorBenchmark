from __future__ import annotations

import os
import platform
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .core import ContractError
from .evidence import sha256_file


MAX_DISTINCT_SAMPLER_ERRORS = 64


@dataclass(frozen=True)
class MemoryCollection:
    process_samples: Tuple[Mapping[str, Any], ...]
    system_pressure_before: Mapping[str, Any]
    system_pressure_after: Mapping[str, Any]
    errors: Tuple[str, ...]

    @property
    def footprint_samples_bytes(self) -> List[int]:
        return [int(item["total_rss_bytes"]) for item in self.process_samples]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "process_samples": [dict(item) for item in self.process_samples],
            "system_pressure_before": dict(self.system_pressure_before),
            "system_pressure_after": dict(self.system_pressure_after),
            "errors": list(self.errors),
        }


def _command_snapshot(command: Sequence[str]) -> Mapping[str, Any]:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=5.0
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": list(command), "available": False, "error": str(error)}
    return {
        "command": list(command),
        "available": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout[-16_384:],
        "stderr": result.stderr[-4096:],
    }


def system_pressure_snapshot() -> Mapping[str, Any]:
    if platform.system() == "Darwin":
        return {
            "platform": "Darwin",
            "memory_pressure": _command_snapshot(["memory_pressure", "-Q"]),
            "vm_stat": _command_snapshot(["vm_stat"]),
        }
    return {
        "platform": platform.system(),
        "proc_meminfo": _command_snapshot(["cat", "/proc/meminfo"]),
    }


class ProcessMemorySampler:
    """Benchmark-owned RSS sampler that never relies on Worker allocator totals."""

    def __init__(self, process_ids: Sequence[int], interval_seconds: float = 0.05) -> None:
        if not process_ids or any(isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 for pid in process_ids):
            raise ContractError("data_plane.process_ids must contain positive process IDs")
        if len(process_ids) != len(set(process_ids)):
            raise ContractError("data_plane.process_ids must not contain duplicates")
        self.process_ids = tuple(process_ids)
        self.interval_seconds = interval_seconds
        self.samples: List[Mapping[str, Any]] = []
        self._error_counts: Dict[str, int] = {}
        self._discarded_error_count = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._before: Mapping[str, Any] = {}

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("process sampler already started")
        self._before = system_pressure_snapshot()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            rss_by_pid: Dict[str, int] = {}
            for pid in self.process_ids:
                try:
                    result = subprocess.run(
                        ["ps", "-o", "rss=", "-p", str(pid)],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=2.0,
                    )
                    value = result.stdout.strip()
                    if not value:
                        raise RuntimeError("process not found")
                    rss_by_pid[str(pid)] = int(value) * 1024
                except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    message = f"pid {pid}: {error}"
                    if message in self._error_counts:
                        self._error_counts[message] += 1
                    elif len(self._error_counts) < MAX_DISTINCT_SAMPLER_ERRORS:
                        self._error_counts[message] = 1
                    else:
                        self._discarded_error_count += 1
            if rss_by_pid:
                self.samples.append(
                    {
                        "monotonic_ns": time.monotonic_ns(),
                        "rss_bytes_by_pid": rss_by_pid,
                        "total_rss_bytes": sum(rss_by_pid.values()),
                    }
                )
            self._stop.wait(self.interval_seconds)

    def stop(self) -> MemoryCollection:
        if self._thread is None:
            raise RuntimeError("process sampler was not started")
        self._stop.set()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            raise RuntimeError("process sampler did not stop")
        errors = [
            (
                message
                if count == 1
                else f"{message} (repeated {count} times)"
            )
            for message, count in self._error_counts.items()
        ]
        if self._discarded_error_count:
            errors.append(
                f"additional sampler errors discarded: {self._discarded_error_count}"
            )
        return MemoryCollection(
            process_samples=tuple(self.samples),
            system_pressure_before=self._before,
            system_pressure_after=system_pressure_snapshot(),
            errors=tuple(errors),
        )


class XctraceCollector:
    """Benchmark-owned Instruments lifecycle and raw trace custody."""

    def __init__(self, executable: Path, attach_pid: int, output_root: Path) -> None:
        try:
            mode = os.lstat(executable).st_mode
        except OSError as error:
            raise RuntimeError(f"xctrace executable is unavailable: {error}") from error
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or not os.access(executable, os.X_OK):
            raise ContractError("xctrace external input must be a directly executable regular file")
        if isinstance(attach_pid, bool) or not isinstance(attach_pid, int) or attach_pid <= 0:
            raise ContractError("xctrace attach PID must be positive")
        self.executable = executable
        self.attach_pid = attach_pid
        self.output_root = output_root
        self.trace_path = output_root / "capture.trace"
        self.process: Optional[subprocess.Popen[str]] = None

    def _base_command(self) -> List[str]:
        return (
            [str(self.executable), "xctrace"]
            if self.executable.name == "xcrun"
            else [str(self.executable)]
        )

    def start(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=False)
        command = [
            *self._base_command(),
            "record",
            "--template",
            "Metal System Trace",
            "--attach",
            str(self.attach_pid),
            "--output",
            str(self.trace_path),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as error:
            raise RuntimeError(f"cannot start xctrace: {error}") from error
        time.sleep(0.25)
        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate()
            raise RuntimeError(
                f"xctrace exited before collection: exit={self.process.returncode}, "
                f"stdout={stdout[-4096:]!r}, stderr={stderr[-4096:]!r}"
            )

    @staticmethod
    def _validate_tree(path: Path) -> None:
        paths = [path] if path.is_file() else [path, *path.rglob("*")]
        for item in paths:
            mode = os.lstat(item).st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ContractError(f"xctrace produced a symlink or special object: {item}")

    def stop(self) -> Mapping[str, Any]:
        if self.process is None:
            raise RuntimeError("xctrace collector was not started")
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            stdout, stderr = self.process.communicate(timeout=30.0)
        except subprocess.TimeoutExpired as error:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5.0)
            raise RuntimeError("xctrace did not finalize within 30 seconds") from error
        if self.process.returncode not in {0, -signal.SIGINT}:
            raise RuntimeError(
                f"xctrace record failed: exit={self.process.returncode}, "
                f"stdout={stdout[-4096:]!r}, stderr={stderr[-4096:]!r}"
            )
        if not self.trace_path.exists():
            raise RuntimeError("xctrace did not produce its trace bundle")
        self._validate_tree(self.trace_path)

        toc_path = self.output_root / "table-of-contents.xml"
        export = subprocess.run(
            [
                *self._base_command(),
                "export",
                "--input",
                str(self.trace_path),
                "--toc",
                "--output",
                str(toc_path),
            ],
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        if export.returncode != 0 or not toc_path.is_file() or toc_path.stat().st_size == 0:
            raise RuntimeError(
                f"xctrace export failed: exit={export.returncode}, "
                f"stdout={export.stdout[-4096:]!r}, stderr={export.stderr[-4096:]!r}"
            )
        archive = Path(
            shutil.make_archive(
                str(self.output_root / "capture"),
                "zip",
                root_dir=str(self.trace_path.parent),
                base_dir=self.trace_path.name,
            )
        )
        if not archive.is_file() or archive.stat().st_size == 0:
            raise RuntimeError("xctrace trace archive is empty")
        return {
            "capture_present": True,
            "attach_pid": self.attach_pid,
            "trace_archive_path": str(archive),
            "trace_archive_size": archive.stat().st_size,
            "trace_archive_sha256": sha256_file(archive),
            "toc_path": str(toc_path),
            "toc_size": toc_path.stat().st_size,
            "toc_sha256": sha256_file(toc_path),
            "record_stdout": stdout[-4096:],
            "record_stderr": stderr[-4096:],
            "export_stdout": export.stdout[-4096:],
            "export_stderr": export.stderr[-4096:],
        }
