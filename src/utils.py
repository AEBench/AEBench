from __future__ import annotations

import io
import re
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from console.dashboard import DisplayEvent, DisplayListener


def safe_name(value: str | None, fallback: str = "unknown") -> str:
    raw = (value or fallback).strip() or fallback
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._").lower() or fallback


class Tee(io.TextIOBase):
    def __init__(self, stream: TextIO, log_path: Path) -> None:
        self._stream = stream
        self._log_path = log_path
        self._file: TextIO | None = None

    def __enter__(self) -> "Tee":
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._log_path.open("a", encoding="utf-8")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def write(self, data: str) -> int:
        written = self._stream.write(data)
        if self._file is not None:
            self._file.write(data)
            self._file.flush()
        return written

    def flush(self) -> None:
        self._stream.flush()
        if self._file is not None:
            self._file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def fileno(self) -> int:
        fileno = getattr(self._stream, "fileno", None)
        if fileno is None:
            raise io.UnsupportedOperation("underlying stream does not support fileno")
        value = fileno()
        if not isinstance(value, int):
            raise io.UnsupportedOperation("underlying stream does not support fileno")
        return value


def send_event(listener: DisplayListener | None, event: DisplayEvent) -> None:
    if listener is None:
        return
    send = getattr(listener, "send", None)
    if callable(send):
        send(event)
