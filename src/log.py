from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, TextIO, cast

import structlog

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger

_INFRA_CAPTURE_ACTIVE: ContextVar[bool] = ContextVar("_INFRA_CAPTURE_ACTIVE", default=False)


def _stderr() -> TextIO:
    s = sys.__stderr__
    if s is None:
        raise RuntimeError("sys.__stderr__ is not available")
    return cast(TextIO, s)


class _DisplayAwareStderr:
    """Stderr wrapper that routes to active display listener."""

    def __init__(self) -> None:
        self._buffer: str = ""

    def write(self, text: str) -> int:
        if _INFRA_CAPTURE_ACTIVE.get():
            self._buffer += text
            self._drain_buffer()
        else:
            if self._buffer:
                self._drain_buffer()
            _stderr().write(text)
        return len(text)

    def flush(self) -> None:
        if _INFRA_CAPTURE_ACTIVE.get():
            self._drain_buffer()
        _stderr().flush()

    @property
    def encoding(self) -> str:
        return getattr(_stderr(), "encoding", "utf-8")

    def isatty(self) -> bool:
        return bool(getattr(_stderr(), "isatty", lambda: False)())

    def fileno(self) -> int:
        return _stderr().fileno()

    def _drain_buffer(self) -> None:
        from console.dashboard import DisplayKind, DisplayPanel, send_display_event, has_active_display_sink

        if not self._buffer:
            return

        if has_active_display_sink():
            for line in self._buffer.splitlines():
                if line.strip():
                    send_display_event(
                        kind=DisplayKind.START.value,
                        panel=DisplayPanel.INFRA.value,
                        text=line.rstrip(),
                    )
        else:
            _stderr().write(self._buffer)
            _stderr().flush()

        self._buffer = ""


_DISPLAY_AWARE_STDERR = _DisplayAwareStderr()


@contextmanager
def activate_infra_capture() -> Iterator[None]:
    token = _INFRA_CAPTURE_ACTIVE.set(True)
    old_stderr = sys.stderr
    sys.stderr = cast(TextIO, _DISPLAY_AWARE_STDERR)
    try:
        yield
    finally:
        _DISPLAY_AWARE_STDERR.flush()
        sys.stderr = old_stderr
        _INFRA_CAPTURE_ACTIVE.reset(token)


def configure_logging(
    *,
    log_level: str = "info",
    log_renderer: str = "console",
) -> None:
    level = _to_logging_level(log_level)
    if log_renderer == "json":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    logging.basicConfig(
        level=level,
        handlers=[logging.StreamHandler(cast(TextIO, _DISPLAY_AWARE_STDERR))],
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=cast(TextIO, _DISPLAY_AWARE_STDERR)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "") -> FilteringBoundLogger:
    return cast("FilteringBoundLogger", structlog.get_logger(name))


def get_console():
    from rich.console import Console

    return Console(file=sys.stdout)


def print_console(text: str, *, markup: bool = False) -> None:
    from console.dashboard import DisplayKind, DisplayPanel, send_display_event, has_active_display_sink
    from rich.text import Text

    if has_active_display_sink():
        plain = Text.from_markup(text).plain if markup else text
        send_display_event(
            kind=DisplayKind.START.value,
            panel=DisplayPanel.STATUS.value,
            text=plain,
        )
    else:
        get_console().print(text, markup=markup)


def _to_logging_level(log_level: str) -> int:
    mapping = {
        "debug": logging.DEBUG,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    return mapping.get(log_level.lower(), logging.INFO)