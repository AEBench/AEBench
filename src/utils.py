from __future__ import annotations

import io
import re
from pathlib import Path
from typing import TextIO


def safe_name(value: str | None, fallback: str = "unknown") -> str:
    raw = (value or fallback).strip()
    if not raw:
        raw = fallback
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._").lower() or fallback


class Tee:
    def __init__(self, stream: TextIO, log_path: Path) -> None:
        self._stream = stream
        self._log_path = log_path
        self._file: TextIO | None = None

    def __enter__(self) -> "Tee":
        self._file = self._log_path.open("a", encoding="utf-8")
        return self

    def __exit__(self, *_args: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def write(self, data: str) -> int:
        written = self._stream.write(data)
        file = self._file
        if file is not None:
            file.write(data)
            file.flush()
        return written

    def flush(self) -> None:
        self._stream.flush()
        file = self._file
        if file is not None:
            file.flush()

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def fileno(self) -> int:
        fileno = getattr(self._stream, "fileno", None)
        if fileno is None:
            raise io.UnsupportedOperation("underlying stream does not support fileno")
        try:
            value = fileno()
        except (AttributeError, io.UnsupportedOperation) as exc:
            raise io.UnsupportedOperation("underlying stream does not support fileno") from exc
        if not isinstance(value, int):
            raise io.UnsupportedOperation("underlying stream does not support fileno")
        return value


def send_event(listener: object, event: object) -> None:
    if listener is None:
        return
    if callable(fn := getattr(listener, "send", None)):
        fn(event)
