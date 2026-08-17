#!/usr/bin/env python3
"""Protocol-agnostic Worker process proxy with deterministic fault modes."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import socket
import struct
import subprocess
import sys
import time
from typing import List, Optional


MODES = (
    "normal",
    "crash-before-start",
    "crash-during-turn",
    "timeout",
    "malformed-frame",
    "incompatible-handshake",
    "duplicate-receipt",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--daemon-fd", type=int)
    parser.add_argument("--worker-fd-env", default="TURNVECTOR_BACKEND_FD")
    parser.add_argument("--duplicate-worker-frame-index", type=int, default=2)
    parser.add_argument("--max-frame-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("worker_command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def resolve_daemon_fd(args: argparse.Namespace) -> int:
    if args.daemon_fd is not None:
        return args.daemon_fd
    value = os.environ.get(args.worker_fd_env)
    if value is None:
        raise RuntimeError(
            f"daemon endpoint fd is missing; pass --daemon-fd or {args.worker_fd_env}"
        )
    return int(value)


def child_command(value: List[str]) -> List[str]:
    command = list(value)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise RuntimeError("a production Worker command is required after --")
    return command


def pop_u32be_frames(buffer: bytearray, max_frame_bytes: int) -> List[bytes]:
    frames: List[bytes] = []
    while len(buffer) >= 4:
        payload_size = struct.unpack(">I", buffer[:4])[0]
        if payload_size > max_frame_bytes:
            raise RuntimeError(
                f"worker declared {payload_size} bytes above {max_frame_bytes} byte fixture bound"
            )
        frame_size = 4 + payload_size
        if len(buffer) < frame_size:
            break
        frames.append(bytes(buffer[:frame_size]))
        del buffer[:frame_size]
    return frames


def injected_frame(mode: str, max_frame_bytes: int) -> bytes:
    if mode == "malformed-frame":
        return struct.pack(">I", max_frame_bytes + 1)
    if mode == "incompatible-handshake":
        # Valid protobuf wire bytes for field 1 with an intentionally impossible
        # protocol-major sentinel. The daemon must reject it before execution.
        payload = b"\x08\xff\xff\xff\xff\x0f"
        return struct.pack(">I", len(payload)) + payload
    raise RuntimeError(f"mode {mode!r} does not define an injected frame")


def relay(args: argparse.Namespace) -> int:
    if args.mode == "crash-before-start":
        return 70
    daemon = socket.socket(fileno=resolve_daemon_fd(args))
    if args.mode == "timeout":
        time.sleep(args.timeout_seconds)
        return 71
    if args.mode in {"malformed-frame", "incompatible-handshake"}:
        daemon.sendall(injected_frame(args.mode, args.max_frame_bytes))
        return 72

    proxy, worker = socket.socketpair()
    environment = dict(os.environ)
    environment[args.worker_fd_env] = str(worker.fileno())
    process: Optional[subprocess.Popen[bytes]] = None
    selector: Optional[selectors.BaseSelector] = None
    try:
        process = subprocess.Popen(
            child_command(args.worker_command),
            env=environment,
            pass_fds=(worker.fileno(),),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )
        worker.close()
        daemon.setblocking(False)
        proxy.setblocking(False)
        selector = selectors.DefaultSelector()
        endpoints = (daemon, proxy)
        destinations = {daemon: proxy, proxy: daemon}
        directions = {daemon: "daemon", proxy: "worker"}
        read_open = {daemon: True, proxy: True}
        pending = {daemon: bytearray(), proxy: bytearray()}
        shutdown_pending = {daemon: False, proxy: False}
        write_closed = {daemon: False, proxy: False}
        pending_limit = max(64 * 1024, args.max_frame_bytes + 4)

        def finish_half_close(endpoint: socket.socket) -> None:
            if (
                shutdown_pending[endpoint]
                and not pending[endpoint]
                and not write_closed[endpoint]
            ):
                try:
                    endpoint.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                write_closed[endpoint] = True

        def refresh_registration(endpoint: socket.socket) -> None:
            events = 0
            destination = destinations[endpoint]
            if read_open[endpoint] and len(pending[destination]) < pending_limit:
                events |= selectors.EVENT_READ
            if pending[endpoint]:
                events |= selectors.EVENT_WRITE
            try:
                key = selector.get_key(endpoint)
            except KeyError:
                if events:
                    selector.register(endpoint, events, endpoint)
            else:
                if events:
                    selector.modify(endpoint, events, endpoint)
                else:
                    selector.unregister(key.fileobj)

        for endpoint in endpoints:
            refresh_registration(endpoint)
        worker_frames = 0
        worker_buffer = bytearray()
        while selector.get_map():
            events = selector.select(timeout=0.25)
            if (
                not events
                and process.poll() is not None
                and not read_open[proxy]
                and not any(pending.values())
            ):
                break
            for key, event_mask in events:
                endpoint = key.data
                destination = destinations[endpoint]
                direction = directions[endpoint]
                if event_mask & selectors.EVENT_READ:
                    try:
                        data = endpoint.recv(64 * 1024)
                    except BlockingIOError:
                        data = None
                    if data == b"":
                        if direction == "worker" and worker_buffer:
                            raise RuntimeError(
                                "worker closed with a partial length-framed message"
                            )
                        read_open[endpoint] = False
                        shutdown_pending[destination] = True
                        finish_half_close(destination)
                    elif data is not None:
                        if args.mode == "crash-during-turn" and direction == "daemon":
                            process.terminate()
                            process.wait(timeout=5)
                            return 73
                        if args.mode == "duplicate-receipt" and direction == "worker":
                            worker_buffer.extend(data)
                            for frame in pop_u32be_frames(
                                worker_buffer, args.max_frame_bytes
                            ):
                                worker_frames += 1
                                pending[destination].extend(frame)
                                if worker_frames == args.duplicate_worker_frame_index:
                                    pending[destination].extend(frame)
                        else:
                            pending[destination].extend(data)
                if event_mask & selectors.EVENT_WRITE and pending[endpoint]:
                    try:
                        written = endpoint.send(pending[endpoint])
                    except BlockingIOError:
                        written = 0
                    if written:
                        del pending[endpoint][:written]
                        finish_half_close(endpoint)
                for changed in endpoints:
                    refresh_registration(changed)
        if (
            args.mode == "duplicate-receipt"
            and worker_frames < args.duplicate_worker_frame_index
        ):
            raise RuntimeError(
                "worker did not emit the selected Receipt frame before termination"
            )
        return process.wait(timeout=5)
    finally:
        if selector is not None:
            selector.close()
        for endpoint in (daemon, proxy, worker):
            try:
                endpoint.close()
            except OSError:
                pass
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    args = parse_args()
    if args.describe:
        print(
            json.dumps(
                {
                    "fixture": "turnvector-worker-proxy-v1",
                    "modes": list(MODES),
                    "framing": "u32be-length-prefixed-protobuf",
                    "duplicate_unit": "complete-worker-frame",
                    "production_protocol_decisions": False,
                },
                sort_keys=True,
            )
        )
        return 0
    return relay(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"worker fixture error: {error}", file=sys.stderr)
        raise SystemExit(74)
