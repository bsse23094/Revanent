"""Deterministic subprocess fixture for controlled-command integration tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import IO


def _write_bytes(stream: IO[bytes], value: bytes) -> None:
    stream.write(value)
    stream.flush()


def main() -> int:
    mode = sys.argv[1]
    arguments = sys.argv[2:]
    if mode == "args":
        print(json.dumps(arguments, ensure_ascii=False))
    elif mode == "cwd":
        print(Path.cwd())
    elif mode == "env":
        print(json.dumps(dict(os.environ), sort_keys=True))
    elif mode == "selected-env":
        print(json.dumps({key: os.environ.get(key) for key in arguments}, sort_keys=True))
    elif mode == "streams":
        print(arguments[0], end="")
        print(arguments[1], end="", file=sys.stderr)
    elif mode == "flood":
        count = int(arguments[0])
        chunk = b"x" * 16_384
        remaining = count
        while remaining:
            current = chunk[:remaining]
            _write_bytes(sys.stdout.buffer, current)
            _write_bytes(sys.stderr.buffer, current.replace(b"x", b"y"))
            remaining -= len(current)
    elif mode == "invalid-bytes":
        _write_bytes(sys.stdout.buffer, b"valid\xfftail")
        _write_bytes(sys.stderr.buffer, b"error\xfetail")
    elif mode == "exit":
        return int(arguments[0])
    elif mode == "stdin":
        _write_bytes(sys.stdout.buffer, sys.stdin.buffer.read())
    elif mode == "block":
        launched = Path(arguments[0])
        release = Path(arguments[1])
        launched.write_text(str(os.getpid()), encoding="ascii")
        while not release.exists():
            time.sleep(0.005)
    elif mode == "write-marker":
        Path(arguments[0]).write_text("launched", encoding="ascii")
    else:
        print("unknown fixture mode", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
