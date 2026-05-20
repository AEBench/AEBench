from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from models import LiveLayoutMode, LiveViewMode, UiMode
from run_control import RunControl

logger = logging.getLogger(__name__)

_WIDTH_TRIPLE = 180
_WIDTH_SPLIT = 120
_MAX_PANEL_LINES = 200
_MIN_MAIN_PANEL_HEIGHT = 8
_MIN_PROGRESS_PANEL_HEIGHT = 5


class DisplayPanel(str, Enum):
	STATUS = "status"
	INFRA = "infra"
	AGENT = "agent"
	OUTPUT = "output"
	PROGRESS = "progress"


class DisplayKind(str, Enum):
	START = "start"
	STATUS = "status"
	ASSISTANT_TEXT = "assistant_text"
	TOOL_CALL = "tool_call"
	TOOL_RESULT = "tool_result"
	RUNNER_OUTPUT = "runner_output"
	ERROR = "error"
	BENCHMARK_PROGRESS = "benchmark_progress"
	PROGRESS = "progress"


_DISPLAY_PANELS: tuple[DisplayPanel, ...] = tuple(DisplayPanel.__members__.values())


class DisplayEvent(BaseModel):
	model_config = ConfigDict(extra="forbid")

	ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
	case_id: str | None = None
	kind: str
	panel: str
	text: str = ""
	tool_name: str | None = None
	command: str | None = None
	is_error: bool = False
	artifact_path: str | None = None
	data: dict[str, Any] = Field(default_factory=dict)


class DisplayListener(Protocol):
	def send(self, event: DisplayEvent) -> None: ...


@dataclass(slots=True)
class DisplayConfig:
	view: LiveViewMode = LiveViewMode.AUTO
	layout: LiveLayoutMode = LiveLayoutMode.AUTO
	interactive: bool = False
	ui: UiMode = UiMode.RICH


@dataclass(slots=True)
class ProgressSource:
	command: str
	log_path: str | None
	source_type: str
	host_path: Path | None = None
	label: str = ""


@dataclass(frozen=True, slots=True)
class DashboardCaseRow:
	case_id: str
	status: str
	score: str
	is_active: bool


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
	title: str
	current_view: LiveViewMode
	current_layout: LiveLayoutMode
	focus_panel: DisplayPanel
	show_help: bool
	active_case: str | None
	status_lines: tuple[str, ...]
	infra_lines: tuple[str, ...]
	agent_lines: tuple[str, ...]
	output_lines: tuple[str, ...]
	raw_lines: tuple[str, ...]
	progress_text: str
	progress_raw: str
	case_rows: tuple[DashboardCaseRow, ...]
	interrupt_state_label: str | None


_ACTIVE_SINK: ContextVar[DisplayListener | None] = cast(
	ContextVar[DisplayListener | None],
	ContextVar("ae_active_display_sink", default=None),
)


def _compute_effective_layout(current_layout: LiveLayoutMode, width: int) -> LiveLayoutMode:
	if current_layout != LiveLayoutMode.AUTO:
		return current_layout
	if width >= _WIDTH_TRIPLE:
		return LiveLayoutMode.TRIPLE
	if width >= _WIDTH_SPLIT:
		return LiveLayoutMode.SPLIT
	return LiveLayoutMode.SINGLE


def _compute_panel_sizes(
	snapshot: DashboardSnapshot,
	*,
	height: int,
	run_control: RunControl | None,
) -> tuple[int, int]:
	recent_status_lines = min(2, len(snapshot.status_lines))
	scoreboard_lines = max(4, len(snapshot.case_rows) + 3)
	header_lines = 5 if run_control is not None else 4
	status_size = max(12, header_lines + scoreboard_lines + recent_status_lines)

	progress_text = snapshot.progress_text
	if not progress_text or progress_text == "No active long-running progress source":
		progress_size = _MIN_PROGRESS_PANEL_HEIGHT
	else:
		progress_size = max(_MIN_PROGRESS_PANEL_HEIGHT, min(8, 4 + progress_text.count("\n")))

	max_reserved = max(0, height - _MIN_MAIN_PANEL_HEIGHT)
	if status_size + progress_size > max_reserved:
		overflow = status_size + progress_size - max_reserved
		reducible_progress = max(0, progress_size - _MIN_PROGRESS_PANEL_HEIGHT)
		progress_reduction = min(reducible_progress, overflow)
		progress_size -= progress_reduction
		overflow -= progress_reduction
		reducible_status = max(0, status_size - 10)
		status_size -= min(reducible_status, overflow)

	return status_size, progress_size


def _stdout() -> Any:
	return sys.__stdout__ or sys.stdout


def active_display_sink() -> DisplayListener | None:
	return _ACTIVE_SINK.get()


def has_active_display_sink() -> bool:
	return active_display_sink() is not None


def active_progress_source() -> ProgressSource | None:
	listener = active_display_sink()
	if listener is None:
		return None
	getter = getattr(listener, "current_progress_source", None)
	if getter is None:
		return None
	source = getter()
	return source if isinstance(source, ProgressSource) else None


@contextmanager
def activate_display_sink(listener: DisplayListener) -> Iterator[None]:
	token = _ACTIVE_SINK.set(listener)
	try:
		yield
	finally:
		_ACTIVE_SINK.reset(token)


def send_display_event(
	*,
	kind: str,
	panel: str,
	text: str = "",
	case_id: str | None = None,
	tool_name: str | None = None,
	command: str | None = None,
	is_error: bool = False,
	artifact_path: str | None = None,
	data: dict[str, Any] | None = None,
) -> None:
	event = DisplayEvent(
		case_id=case_id,
		kind=kind,
		panel=panel,
		text=text,
		tool_name=tool_name,
		command=command,
		is_error=is_error,
		artifact_path=artifact_path,
		data=data or {},
	)
	listener = active_display_sink()
	if listener is not None:
		listener.send(event)
	else:
		from .dashboard_render import _fallback_render_event

		_fallback_render_event(event)
