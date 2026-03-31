from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..display import DisplayEvent, DisplayKind
from ..log import get_console


class EventSink(Protocol):
    def emit(self, event: DisplayEvent) -> None: ...


class NullEventSink:
    def emit(self, event: DisplayEvent) -> None:
        _ = event


class CompositeEventSink:
    def __init__(self, sinks: list[EventSink]) -> None:
        self._sinks = sinks

    def emit(self, event: DisplayEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)


class JsonlEventSink:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: DisplayEvent) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json())
            handle.write("\n")


class ConsoleEventSink:
    def emit(self, event: DisplayEvent) -> None:
        payload = event.text or json.dumps(event.data, ensure_ascii=False)
        get_console().print(payload)


def emit_event(sink: EventSink | None, event: DisplayEvent) -> None:
    (sink or NullEventSink()).emit(event)


class EventMiddleware(Protocol):
    """Intercepts events before they reach the underlying sink."""

    def process(self, event: DisplayEvent, next_sink: EventSink) -> None: ...


class MiddlewareEventSink:
    """Chains a list of EventMiddleware instances in front of a terminal sink."""

    def __init__(self, sink: EventSink, middleware: list[EventMiddleware]) -> None:
        self._sink = sink
        self._middleware = list(middleware)

    def emit(self, event: DisplayEvent) -> None:
        _run_middleware(event, self._middleware, self._sink)


def _run_middleware(
    event: DisplayEvent,
    middleware: list[EventMiddleware],
    terminal: EventSink,
) -> None:
    if not middleware:
        terminal.emit(event)
        return
    head, *tail = middleware

    class _Next:
        def emit(self, evt: DisplayEvent) -> None:
            _run_middleware(evt, tail, terminal)

    head.process(event, _Next())


class KindFilterMiddleware:
    """Drops events whose kind is in the exclusion set."""

    def __init__(self, excluded_kinds: set[str]) -> None:
        self._excluded = excluded_kinds

    def process(self, event: DisplayEvent, next_sink: EventSink) -> None:
        if event.kind not in self._excluded:
            next_sink.emit(event)


class CaseIdEnrichMiddleware:
    """Back-fills a default case_id on events that arrive without one."""

    def __init__(self, default_case_id: str) -> None:
        self._default = default_case_id

    def process(self, event: DisplayEvent, next_sink: EventSink) -> None:
        if event.case_id is None:
            event = event.model_copy(update={"case_id": self._default})
        next_sink.emit(event)
