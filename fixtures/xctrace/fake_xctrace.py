#!/usr/bin/env python3
"""Non-claimable xctrace CLI fixture for collector lifecycle tests."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path


def argument(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError) as error:
        raise SystemExit(f"missing {name}") from error


def record() -> int:
    output = Path(argument("--output"))
    output.mkdir(parents=True)
    (output / "fixture.trace-data").write_bytes(b"non-claimable-xctrace-fixture\n")
    stopping = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopping
        del signum, frame
        stopping = True

    signal.signal(signal.SIGINT, stop)
    while not stopping:
        time.sleep(0.01)
    return 0


def export() -> int:
    output = Path(argument("--output"))
    output.write_text(
        "<?xml version=\"1.0\"?><trace-toc><run number=\"1\">"
        "<data><table schema=\"metal-system-trace\"/></data>"
        "</run></trace-toc>\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    if sys.argv[1] == "record":
        return record()
    if sys.argv[1] == "export":
        return export()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
