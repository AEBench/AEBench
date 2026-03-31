from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..display import DisplayEvent
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
