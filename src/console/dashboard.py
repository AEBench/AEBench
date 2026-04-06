from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field
from rich import box
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .constants import (
 DISPLAY_TOOL_OUTPUT_HEAD_LINES,
 DISPLAY_TOOL_OUTPUT_INLINE_BYTES,
 DISPLAY_TOOL_OUTPUT_TAIL_LINES,
)
from .models import LiveLayoutMode, LiveViewMode, UiMode
from .run_control import RunControl

_WIDTH_TRIPLE = 180
_WIDTH_SPLIT = 120
_MAX_PANEL_LINES = 200
_RESIZE_POLL_INTERVAL_SEC = 0.2
_MIN_MAIN_PANEL_HEIGHT = 8
_MIN_PROGRESS_PANEL_HEIGHT = 5
_StateUpdateT = TypeVar("_StateUpdateT")


class DisplayPanel(str, Enum):
	STATUS = "status"
	INFRA = "infra"
	AGENT = "agent"
	OUTPUT = "output"
	PROGRESS = "progress"


class DisplayKind(str, Enum):
	LIFECYCLE = "lifecycle"
	STATUS = "status"
	ASSISTANT_TEXT = "assistant_text"
	TOOL_CALL = "tool_call"
	TOOL_RESULT = "tool_result"
	RUNNER_OUTPUT = "runner_output"
	ERROR = "error"
	BENCHMARK_PROGRESS = "benchmark_progress"
	PROGRESS = "progress"


_DISPLAY_PANELS: tuple["DisplayPanel", ...] = tuple(DisplayPanel.__members__.values())


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


class DisplaySink(Protocol):
	def emit(self, event: DisplayEvent) -> None: ...


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


_ACTIVE_SINK: ContextVar[DisplaySink | None] = cast(
 ContextVar[DisplaySink | None],
 ContextVar("ae_active_display_sink", default=None),
)


def _stdout() -> Any:
	return sys.__stdout__ or sys.stdout


def _new_live(*, renderable: RenderableType, console: Console) -> Any:
	rich_live = __import__("rich.live", fromlist=["Live"])
	rich_live_cls = getattr(rich_live, "Live")

	return rich_live_cls(
	 renderable,
	 console=console,
	 refresh_per_second=8,
	 transient=False,
	 auto_refresh=False,
	)


def active_display_sink() -> DisplaySink | None:
	return _ACTIVE_SINK.get()


def has_active_display_sink() -> bool:
	return active_display_sink() is not None


def active_progress_source() -> ProgressSource | None:
	sink = active_display_sink()
	if sink is None:
		return None
	getter = getattr(sink, "current_progress_source", None)
	if getter is None:
		return None
	source = getter()
	return source if isinstance(source, ProgressSource) else None


@contextmanager
def activate_display_sink(sink: DisplaySink) -> Iterator[None]:
	token = _ACTIVE_SINK.set(sink)
	try:
		yield
	finally:
		_ACTIVE_SINK.reset(token)


def emit_display_event(
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
	sink = active_display_sink()
	if sink is not None:
		sink.emit(event)
	else:
		_fallback_render_event(event)


class DashboardState:
	def __init__(
	 self,
	 *,
	 config: DisplayConfig,
	 title: str,
	 selected_cases: list[str] | None = None,
	 run_control: RunControl | None = None,
	) -> None:
		self._title = title
		self._run_control = run_control
		self._status_lines: deque[str] = deque(maxlen=_MAX_PANEL_LINES)
		self._infra_lines: deque[str] = deque(maxlen=_MAX_PANEL_LINES)
		self._agent_lines: deque[str] = deque(maxlen=_MAX_PANEL_LINES)
		self._output_lines: deque[str] = deque(maxlen=_MAX_PANEL_LINES)
		self._progress_text = "No active long-running progress source"
		self._progress_raw = ""
		self._raw_lines: deque[str] = deque(maxlen=_MAX_PANEL_LINES)
		self._active_case: str | None = None
		self._selected_cases = selected_cases or []
		self._case_status: dict[str, str] = {case_id: "pending" for case_id in self._selected_cases}
		self._case_score: dict[str, str] = {}
		self._current_view = (
		 LiveViewMode.COMPACT if config.view == LiveViewMode.AUTO else config.view
		)
		self._current_layout = config.layout
		self._focus_panel = DisplayPanel.AGENT
		self._show_help = False

	def apply_event(self, event: DisplayEvent) -> None:
		if event.case_id:
			self._active_case = event.case_id
		if event.kind == DisplayKind.BENCHMARK_PROGRESS:
			self._update_benchmark(event)
		line = _format_event_line(event, self._current_view)
		raw_line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
		self._raw_lines.append(raw_line)
		if event.panel == DisplayPanel.STATUS:
			self._status_lines.append(line)
		elif event.panel == DisplayPanel.INFRA:
			self._infra_lines.append(line)
		elif event.panel == DisplayPanel.AGENT:
			self._agent_lines.append(line)
		elif event.panel == DisplayPanel.PROGRESS:
			self._progress_text = line
			self._progress_raw = raw_line
		else:
			self._output_lines.append(line)

	def set_layout(self, layout: LiveLayoutMode) -> None:
		self._current_layout = layout

	def cycle_layout(self) -> LiveLayoutMode:
		next_layout = {
		 LiveLayoutMode.AUTO: LiveLayoutMode.SINGLE,
		 LiveLayoutMode.SINGLE: LiveLayoutMode.SPLIT,
		 LiveLayoutMode.SPLIT: LiveLayoutMode.TRIPLE,
		 LiveLayoutMode.TRIPLE: LiveLayoutMode.AUTO,
		}[self._current_layout]
		self._current_layout = next_layout
		return next_layout

	def toggle_view(self) -> LiveViewMode:
		self._current_view = (
		 LiveViewMode.RAW if self._current_view == LiveViewMode.COMPACT else LiveViewMode.COMPACT
		)
		return self._current_view

	def cycle_focus(self) -> DisplayPanel:
		current_index = _DISPLAY_PANELS.index(self._focus_panel)
		self._focus_panel = _DISPLAY_PANELS[(current_index + 1) % len(_DISPLAY_PANELS)]
		return self._focus_panel

	def toggle_help(self) -> bool:
		self._show_help = not self._show_help
		return self._show_help

	def effective_layout(self, width: int) -> LiveLayoutMode:
		if self._current_layout != LiveLayoutMode.AUTO:
			return self._current_layout
		if width >= _WIDTH_TRIPLE:
			return LiveLayoutMode.TRIPLE
		if width >= _WIDTH_SPLIT:
			return LiveLayoutMode.SPLIT
		return LiveLayoutMode.SINGLE

	def status_height(self, *, run_control: RunControl | None) -> int:
		recent_status_lines = min(2, len(self._status_lines))
		scoreboard_lines = max(4, len(self._selected_cases) + 3)
		header_lines = 5 if run_control is not None else 4
		return max(12, header_lines + scoreboard_lines + recent_status_lines)

	def progress_height(self) -> int:
		if (
		 not self._progress_text
		 or self._progress_text == "No active long-running progress source"
		):
			return _MIN_PROGRESS_PANEL_HEIGHT
		return max(_MIN_PROGRESS_PANEL_HEIGHT, min(8, 4 + self._progress_text.count("\n")))

	def snapshot(self) -> DashboardSnapshot:
		case_rows = tuple(
		 DashboardCaseRow(
		  case_id=case_id,
		  status=self._case_status.get(case_id, "pending"),
		  score=self._case_score.get(case_id, "-"),
		  is_active=case_id == self._active_case,
		 )
		 for case_id in self._selected_cases
		)
		interrupt_state_label = (
		 self._run_control.state_label() if self._run_control is not None else None
		)
		return DashboardSnapshot(
		 title=self._title,
		 current_view=self._current_view,
		 current_layout=self._current_layout,
		 focus_panel=self._focus_panel,
		 show_help=self._show_help,
		 active_case=self._active_case,
		 status_lines=tuple(self._status_lines),
		 infra_lines=tuple(self._infra_lines),
		 agent_lines=tuple(self._agent_lines),
		 output_lines=tuple(self._output_lines),
		 raw_lines=tuple(self._raw_lines),
		 progress_text=self._progress_text,
		 progress_raw=self._progress_raw,
		 case_rows=case_rows,
		 interrupt_state_label=interrupt_state_label,
		)

	def _update_benchmark(self, event: DisplayEvent) -> None:
		case_id = event.data.get("case_id")
		if isinstance(case_id, str):
			self._case_status[case_id] = str(event.data.get("status", "running"))
			score = event.data.get("score")
			expected = event.data.get("expected_score")
			if score is not None and expected is not None:
				self._case_score[case_id] = f"{score}/{expected}"


class BaseDashboardDisplay:
	def __init__(
	 self,
	 *,
	 config: DisplayConfig,
	 title: str,
	 selected_cases: list[str] | None = None,
	 run_control: RunControl | None = None,
	) -> None:
		self._config = config
		self._run_control = run_control
		self._state = DashboardState(
		 config=config,
		 title=title,
		 selected_cases=selected_cases,
		 run_control=run_control,
		)
		self._state_lock = threading.RLock()
		self._closed = False

	def __enter__(self) -> "BaseDashboardDisplay":
		return self

	def __exit__(self, *_args: object) -> None:
		self.close()

	def close(self) -> None:
		with self._state_lock:
			self._closed = True

	def emit(self, event: DisplayEvent) -> None:
		snapshot: DashboardSnapshot | None = None
		with self._state_lock:
			if self._closed:
				return
			self._state.apply_event(event)
			snapshot = self._state.snapshot()
		if snapshot is not None:
			self._dispatch_snapshot(snapshot)

	def toggle_view(self) -> LiveViewMode:
		return self._update_state(
		 lambda state: state.toggle_view(),
		 fallback=self.snapshot().current_view,
		)

	def set_layout(self, layout: LiveLayoutMode) -> LiveLayoutMode:
		def _set_layout(state: DashboardState) -> LiveLayoutMode:
			state.set_layout(layout)
			return layout

		return self._update_state(
		 _set_layout,
		 fallback=self.snapshot().current_layout,
		)

	def cycle_layout(self) -> LiveLayoutMode:
		return self._update_state(
		 lambda state: state.cycle_layout(),
		 fallback=self.snapshot().current_layout,
		)

	def cycle_focus(self) -> DisplayPanel:
		return self._update_state(
		 lambda state: state.cycle_focus(),
		 fallback=self.snapshot().focus_panel,
		)

	def toggle_help(self) -> bool:
		return self._update_state(
		 lambda state: state.toggle_help(),
		 fallback=self.snapshot().show_help,
		)

	def request_interrupt(self, source: str = "ui") -> str | None:
		run_control = self._run_control
		if run_control is None:
			return None
		run_control.request_interrupt(source)
		self._dispatch_snapshot(self.snapshot())
		return run_control.state_label()

	def snapshot(self) -> DashboardSnapshot:
		with self._state_lock:
			return self._state.snapshot()

	def _update_state(
	 self,
	 mutator: Callable[[DashboardState], _StateUpdateT],
	 *,
	 fallback: _StateUpdateT,
	) -> _StateUpdateT:
		with self._state_lock:
			if self._closed:
				return fallback
			result = mutator(self._state)
			snapshot = self._state.snapshot()
		self._dispatch_snapshot(snapshot)
		return result

	def _dispatch_snapshot(self, snapshot: DashboardSnapshot) -> None:
		return None


class DashboardDisplay(BaseDashboardDisplay):
	def __init__(
	 self,
	 *,
	 config: DisplayConfig,
	 title: str,
	 selected_cases: list[str] | None = None,
	 run_control: RunControl | None = None,
	) -> None:
		super().__init__(
		 config=config,
		 title=title,
		 selected_cases=selected_cases,
		 run_control=run_control,
		)
		self._live: Any | None = None
		self._console = Console(file=_stdout(), highlight=False)
		self._size_stop = threading.Event()
		self._size_thread: threading.Thread | None = None
		self._last_size: tuple[int, int] | None = None

	def __enter__(self) -> "DashboardDisplay":
		with self._state_lock:
			self._closed = False
			self._live = _new_live(renderable=self._render(), console=self._console)
			self._live.start()
			self._last_size = (self._console.size.width, self._console.size.height)
			self._start_size_watcher()
		return self

	def close(self) -> None:
		self._stop_size_watcher()
		with self._state_lock:
			if self._closed:
				return
			self._closed = True
			if self._live is not None:
				self._live.stop()
				self._live = None

	def _dispatch_snapshot(self, snapshot: DashboardSnapshot) -> None:
		if not self._closed and self._live is not None:
			self._live.update(self._render(snapshot=snapshot), refresh=True)

	def _refresh(self) -> None:
		self._dispatch_snapshot(self.snapshot())

	def _render(self, *, snapshot: DashboardSnapshot | None = None) -> Layout | Panel | Group:
		current = snapshot or self.snapshot()
		width = self._console.size.width
		layout_kind = self._state.effective_layout(width)
		rendered: Layout | Panel
		if layout_kind == LiveLayoutMode.SINGLE:
			rendered = self._render_single(current)
		elif layout_kind == LiveLayoutMode.SPLIT:
			rendered = self._render_split(current)
		else:
			rendered = self._render_triple(current)
		if current.show_help:
			return Group(rendered, self._help_panel())
		return rendered

	def _render_single(self, snapshot: DashboardSnapshot | None = None) -> Layout:
		current = snapshot or self.snapshot()
		status_size, progress_size = self._panel_sizes(current)
		layout = Layout(name="root")
		layout.split_column(
		 Layout(self._status_panel(current), name="status", size=status_size),
		 Layout(self._focused_panel(current), name="main"),
		 Layout(self._progress_panel(current), name="progress", size=progress_size),
		)
		return layout

	def _render_split(self, snapshot: DashboardSnapshot | None = None) -> Layout:
		current = snapshot or self.snapshot()
		status_size, progress_size = self._panel_sizes(current)
		layout = Layout(name="root")
		layout.split_column(
		 Layout(self._status_panel(current), name="status", size=status_size),
		 Layout(name="body"),
		 Layout(self._progress_panel(current), name="progress", size=progress_size),
		)
		layout["body"].split_row(
		 Layout(self._agent_panel(current), name="agent"),
		 Layout(self._output_panel(current), name="output"),
		)
		return layout

	def _render_triple(self, snapshot: DashboardSnapshot | None = None) -> Layout:
		current = snapshot or self.snapshot()
		status_size, progress_size = self._panel_sizes(current)
		layout = Layout(name="root")
		layout.split_row(
		 Layout(name="left", ratio=1),
		 Layout(name="main", ratio=4),
		)
		layout["left"].split_column(
		 Layout(self._status_panel(current), name="status", size=status_size),
		 Layout(self._infra_panel(current), name="infra"),
		)
		layout["main"].split_column(
		 Layout(name="body"),
		 Layout(self._progress_panel(current), name="progress", size=progress_size),
		)
		layout["main"]["body"].split_row(
		 Layout(self._agent_panel(current), name="agent", ratio=2),
		 Layout(self._output_panel(current), name="output", ratio=2),
		)
		return layout

	def _status_panel(self, snapshot: DashboardSnapshot | None = None) -> Panel:
		current = snapshot or self.snapshot()
		body = Group(
		 self._header_text(current),
		 self._scoreboard_table(current),
		 self._lines_text(current.status_lines, raw=False),
		)
		return self._panel(
		 body,
		 title="STATUS",
		 subtitle=self._status_subtitle(current),
		 border_style="bright_blue",
		)

	def _agent_panel(self, snapshot: DashboardSnapshot | None = None) -> Panel:
		current = snapshot or self.snapshot()
		lines = (
		 current.raw_lines if current.current_view == LiveViewMode.RAW else current.agent_lines
		)
		return self._panel(
		 self._lines_text(lines, raw=current.current_view == LiveViewMode.RAW),
		 title="AGENT",
		 subtitle="assistant transcript",
		 border_style="bright_cyan",
		)

	def _infra_panel(self, snapshot: DashboardSnapshot | None = None) -> Panel:
		current = snapshot or self.snapshot()
		lines = (
		 current.raw_lines if current.current_view == LiveViewMode.RAW else current.infra_lines
		)
		return self._panel(
		 self._lines_text(lines, raw=current.current_view == LiveViewMode.RAW),
		 title="INFRA",
		 subtitle="runtime / third-party",
		 border_style="magenta",
		)

	def _output_panel(self, snapshot: DashboardSnapshot | None = None) -> Panel:
		current = snapshot or self.snapshot()
		lines = (
		 current.raw_lines if current.current_view == LiveViewMode.RAW else current.output_lines
		)
		return self._panel(
		 self._lines_text(lines, raw=current.current_view == LiveViewMode.RAW),
		 title="COMMAND / OUTPUT",
		 subtitle="tool calls and results",
		 border_style="yellow",
		)

	def _progress_panel(self, snapshot: DashboardSnapshot | None = None) -> Panel:
		current = snapshot or self.snapshot()
		if current.current_view == LiveViewMode.RAW:
			body = Text(
			 current.progress_raw or "No active long-running progress source", style="grey62"
			)
		else:
			body = Text(current.progress_text or "No active long-running progress source")
		return self._panel(
		 body,
		 title="PROGRESS",
		 subtitle="long-running log summary",
		 border_style="bright_green",
		)

	def _focused_panel(self, snapshot: DashboardSnapshot | None = None) -> Panel:
		current = snapshot or self.snapshot()
		if current.focus_panel == DisplayPanel.STATUS:
			return self._status_panel(current)
		if current.focus_panel == DisplayPanel.INFRA:
			return self._infra_panel(current)
		if current.focus_panel == DisplayPanel.OUTPUT:
			return self._output_panel(current)
		if current.focus_panel == DisplayPanel.PROGRESS:
			return self._progress_panel(current)
		return self._agent_panel(current)

	def _header_text(self, snapshot: DashboardSnapshot | None = None) -> Text:
		current = snapshot or self.snapshot()
		text = Text()
		layout_kind = self._state.effective_layout(self._console.size.width)
		active_case = current.active_case
		run_control = self._run_control
		text.append(" AE ", style="bold black on bright_blue")
		text.append("  ")
		text.append(current.title, style="bold bright_white")
		if active_case is not None:
			text.append("\n")
			text.append(" ACTIVE ", style="bold black on cyan")
			text.append(" ")
			text.append(active_case, style="bold cyan")
		text.append(
		 "\n",
		)
		text.append(" VIEW ", style="bold black on grey70")
		text.append(
		 f" {current.current_view.value}",
		 style="grey70",
		)
		text.append("   ")
		text.append(" LAYOUT ", style="bold black on grey70")
		text.append(
		 f" {layout_kind.value}",
		 style="grey70",
		)
		if layout_kind == LiveLayoutMode.SINGLE:
			text.append("   ")
			text.append(" FOCUS ", style="bold black on grey70")
			text.append(f" {current.focus_panel.value}", style="grey70")
		if run_control is not None:
			text.append("\n")
			text.append(" INTERRUPT ", style="bold black on grey70")
			text.append(
			 f" {run_control.state_label()}",
			 style=_interrupt_style(run_control),
			)
		return text

	def _scoreboard_table(self, snapshot: DashboardSnapshot | None = None) -> Table:
		current = snapshot or self.snapshot()
		table = Table(
		 show_header=True,
		 expand=True,
		 box=box.SIMPLE_HEAVY,
		 padding=(0, 1),
		)
		table.add_column("Case", style="bold bright_white")
		table.add_column("Status", style="bold")
		table.add_column("Score", justify="right")
		for row in current.case_rows:
			case_style = "bold bright_white" if row.is_active else "white"
			table.add_row(
			 Text(row.case_id, style=case_style),
			 Text(row.status, style=_status_style(row.status)),
			 Text(row.score, style="bright_white"),
			)
		return table

	def _lines_text(self, lines: tuple[str, ...], *, raw: bool) -> Text:
		text = Text()
		for index, line in enumerate(lines):
			if index:
				text.append("\n")
			text.append_text(_styled_line(line, raw=raw))
		return text

	def _status_subtitle(self, snapshot: DashboardSnapshot | None = None) -> str:
		current = snapshot or self.snapshot()
		active = current.active_case or "-"
		return f"active={active}  cases={len(current.case_rows)}"

	def _panel(
	 self,
	 body: Group | Text,
	 *,
	 title: str,
	 subtitle: str,
	 border_style: str,
	) -> Panel:
		return Panel(
		 body,
		 title=Text(title, style=f"bold {border_style}"),
		 title_align="left",
		 subtitle=Text(subtitle, style="grey62"),
		 subtitle_align="left",
		 border_style=border_style,
		 box=box.ROUNDED,
		 padding=(0, 1),
		)

	def _help_panel(self) -> Panel:
		body = Text()
		body.append("v", style="bold bright_cyan")
		body.append(" toggle compact/raw\n", style="grey78")
		body.append("1/2/3", style="bold yellow")
		body.append(" set single/split/triple layout\n", style="grey78")
		body.append("l", style="bold yellow")
		body.append(" cycle layout\n", style="grey78")
		body.append("f", style="bold bright_blue")
		body.append(" change focus (single layout only)\n", style="grey78")
		body.append("q", style="bold red")
		body.append(" graceful stop; press again to force stop\n", style="grey78")
		body.append("?", style="bold white")
		body.append(" toggle help", style="grey78")
		return Panel(
		 body,
		 title=Text("HELP", style="bold white"),
		 border_style="grey62",
		 box=box.ROUNDED,
		 padding=(0, 1),
		)

	def _effective_layout_kind(self, width: int) -> LiveLayoutMode:
		return self._state.effective_layout(width)

	def _start_size_watcher(self) -> None:
		self._size_stop.clear()
		self._size_thread = threading.Thread(
		 target=self._size_watch_loop,
		 name="artevalbench-dashboard-size",
		 daemon=True,
		)
		self._size_thread.start()

	def _stop_size_watcher(self) -> None:
		self._size_stop.set()
		if self._size_thread is not None:
			self._size_thread.join(timeout=1.0)
			self._size_thread = None

	def _size_watch_loop(self) -> None:
		while not self._size_stop.is_set():
			with self._state_lock:
				current_size = (self._console.size.width, self._console.size.height)
				if current_size != self._last_size:
					self._last_size = current_size
					self._refresh()
			time.sleep(_RESIZE_POLL_INTERVAL_SEC)

	def _panel_sizes(self, snapshot: DashboardSnapshot | None = None) -> tuple[int, int]:
		current = snapshot or self.snapshot()
		height = self._console.size.height
		status_size = self._desired_status_height(current)
		progress_size = self._desired_progress_height(current)
		max_reserved = max(0, height - _MIN_MAIN_PANEL_HEIGHT)
		if status_size + progress_size > max_reserved:
			overflow = status_size + progress_size - max_reserved
			reducible_progress = max(0, progress_size - _MIN_PROGRESS_PANEL_HEIGHT)
			progress_reduction = min(reducible_progress, overflow)
			progress_size -= progress_reduction
			overflow -= progress_reduction
			min_status = 10
			reducible_status = max(0, status_size - min_status)
			status_size -= min(reducible_status, overflow)
		return status_size, progress_size

	def _desired_progress_height(self, snapshot: DashboardSnapshot | None = None) -> int:
		current = snapshot or self.snapshot()
		if (
		 not current.progress_text
		 or current.progress_text == "No active long-running progress source"
		):
			return _MIN_PROGRESS_PANEL_HEIGHT
		return max(_MIN_PROGRESS_PANEL_HEIGHT, min(8, 4 + current.progress_text.count("\n")))

	def _desired_status_height(self, snapshot: DashboardSnapshot | None = None) -> int:
		current = snapshot or self.snapshot()
		recent_status_lines = min(2, len(current.status_lines))
		scoreboard_lines = max(4, len(current.case_rows) + 3)
		header_lines = 5 if self._run_control is not None else 4
		return max(12, header_lines + scoreboard_lines + recent_status_lines)


def create_dashboard_display(
 *,
 config: DisplayConfig,
 title: str,
 selected_cases: list[str] | None = None,
 run_control: RunControl | None = None,
) -> BaseDashboardDisplay | None:
	if config.ui == UiMode.NONE:
		return None
	if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
		return None
	if config.ui == UiMode.TEXTUAL:
		from .tui import TextualDashboardDisplay

		return TextualDashboardDisplay(
		 config=config,
		 title=title,
		 selected_cases=selected_cases,
		 run_control=run_control,
		)
	return DashboardDisplay(
	 config=config,
	 title=title,
	 selected_cases=selected_cases,
	 run_control=run_control,
	)


class CaseDisplaySession:
	def __init__(
	 self,
	 *,
	 case_id: str,
	 output_dir: Path,
	 log_path: Path,
	 transcript_path: Path,
	 rendered_log_path: Path,
	 runner_log_path: Path,
	 infra_log_path: Path,
	 progress_log_path: Path,
	 tool_output_dir: Path,
	 workspace_path: Path,
	 runtime_workspace_path: str,
	 dashboard: BaseDashboardDisplay | None = None,
	 fallback_to_console: bool = True,
	) -> None:
		self._case_id = case_id
		self._output_dir = output_dir
		self._log_path = log_path
		self._transcript_path = transcript_path
		self._rendered_log_path = rendered_log_path
		self._runner_log_path = runner_log_path
		self._infra_log_path = infra_log_path
		self._progress_log_path = progress_log_path
		self._tool_output_dir = tool_output_dir
		self._workspace_path = workspace_path
		self._runtime_workspace_path = runtime_workspace_path
		self._dashboard = dashboard
		self._fallback_to_console = fallback_to_console
		self._tool_output_seq = 0
		self._lock = threading.RLock()
		self._progress_source: ProgressSource | None = None
		self._progress_stop = threading.Event()
		self._progress_thread: threading.Thread | None = None
		self._last_progress_signature: str | None = None
		self._last_progress_emit_at = 0.0
		self._last_progress_poll_at = 0.0

	@property
	def has_dashboard(self) -> bool:
		return self._dashboard is not None

	def current_progress_source(self) -> ProgressSource | None:
		with self._lock:
			return self._progress_source

	def update_workspace_context(
	 self, *, workspace_path: Path, runtime_workspace_path: str
	) -> None:
		with self._lock:
			self._workspace_path = workspace_path
			self._runtime_workspace_path = runtime_workspace_path

	def detach_dashboard(self, *, close: bool = False) -> None:
		with self._lock:
			dashboard = self._dashboard
			self._dashboard = None
		if close and dashboard is not None:
			dashboard.close()

	def __enter__(self) -> "CaseDisplaySession":
		self._output_dir.mkdir(parents=True, exist_ok=True)
		self._tool_output_dir.mkdir(parents=True, exist_ok=True)
		self._transcript_path.write_text("", encoding="utf-8")
		self._rendered_log_path.write_text("", encoding="utf-8")
		self._runner_log_path.write_text("", encoding="utf-8")
		self._infra_log_path.write_text("", encoding="utf-8")
		self._progress_log_path.write_text("", encoding="utf-8")
		if not self._log_path.exists():
			self._log_path.write_text("", encoding="utf-8")
		self._progress_stop.clear()
		self._progress_thread = threading.Thread(
		 target=self._progress_loop,
		 name=f"artevalbench-progress-{self._case_id}",
		 daemon=True,
		)
		self._progress_thread.start()
		return self

	def __exit__(self, *_args: object) -> None:
		self._progress_stop.set()
		if self._progress_thread is not None:
			self._progress_thread.join(timeout=1.0)
			self._progress_thread = None
		return None

	def emit(self, event: DisplayEvent) -> None:
		with self._lock:
			normalized = self._normalize_event(event)
			self._write_event(normalized)

	def _write_event(self, event: DisplayEvent) -> None:
		with self._transcript_path.open("a", encoding="utf-8") as handle:
			handle.write(event.model_dump_json() + "\n")
		if event.kind in {DisplayKind.LIFECYCLE, DisplayKind.STATUS, DisplayKind.ERROR}:
			with self._log_path.open("a", encoding="utf-8") as handle:
				handle.write(_compact_line(event) + "\n")
		if event.kind == DisplayKind.RUNNER_OUTPUT:
			with self._runner_log_path.open("a", encoding="utf-8") as handle:
				handle.write(event.text)
				if event.text and not event.text.endswith("\n"):
					handle.write("\n")
		if event.panel == DisplayPanel.INFRA:
			with self._infra_log_path.open("a", encoding="utf-8") as handle:
				handle.write(_compact_line(event) + "\n")
		if event.panel == DisplayPanel.PROGRESS:
			with self._progress_log_path.open("a", encoding="utf-8") as handle:
				handle.write(_compact_line(event) + "\n")
		if event.panel in {DisplayPanel.AGENT, DisplayPanel.OUTPUT} and (
		 event.kind != DisplayKind.RUNNER_OUTPUT
		):
			with self._rendered_log_path.open("a", encoding="utf-8") as handle:
				handle.write(_compact_line(event) + "\n")
		if self._dashboard is not None:
			self._dashboard.emit(event)
		elif self._fallback_to_console:
			_fallback_render_event(event)

	def _normalize_event(self, event: DisplayEvent) -> DisplayEvent:
		payload = dict(event.data)
		if event.case_id is None:
			event = event.model_copy(update={"case_id": self._case_id})
		if event.kind == DisplayKind.TOOL_CALL and event.tool_name == "Bash":
			self._update_progress_source(event)
		if event.kind == DisplayKind.TOOL_RESULT and not event.text and event.artifact_path is None:
			text, artifact_path = self._summarize_tool_result(event, payload)
			payload = _payload_without_large_text(payload)
			return event.model_copy(
			 update={
			  "text": text,
			  "artifact_path": artifact_path,
			  "data": payload,
			 }
			)
		return event

	def _update_progress_source(self, event: DisplayEvent) -> None:
		command = event.command or ""
		if not command:
			return
		source = _detect_progress_source(
		 command=command,
		 workspace_path=self._workspace_path,
		 runtime_workspace_path=self._runtime_workspace_path,
		)
		if source is None:
			return
		self._progress_source = source
		self._last_progress_signature = None
		self._last_progress_emit_at = 0.0
		self._last_progress_poll_at = 0.0
		if source.source_type == "untracked":
			self._write_event(
			 DisplayEvent(
			  case_id=self._case_id,
			  kind=DisplayKind.PROGRESS,
			  panel=DisplayPanel.PROGRESS,
			  text=(
			   f"Long-running command has no explicit log file\nCOMMAND: {source.command}"
			  ),
			  command=source.command,
			  data={"source_type": source.source_type},
			 )
			)

	def _summarize_tool_result(
	 self,
	 event: DisplayEvent,
	 payload: dict[str, Any],
	) -> tuple[str, str | None]:
		stdout = _coerce_text(payload.get("stdout"))
		stderr = _coerce_text(payload.get("stderr"))
		interrupted = bool(payload.get("interrupted"))
		is_image = bool(payload.get("isImage"))
		full_text = _combine_output(stdout, stderr)
		byte_len = len(full_text.encode("utf-8"))
		line_count = len(full_text.splitlines()) if full_text else 0
		artifact_path: str | None = None
		rendered_output = full_text
		if byte_len > DISPLAY_TOOL_OUTPUT_INLINE_BYTES:
			self._tool_output_seq += 1
			filename = (
			 f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-"
			 f"{(event.tool_name or 'tool').lower()}-{self._tool_output_seq}.log"
			)
			target = self._tool_output_dir / filename
			target.write_text(full_text, encoding="utf-8")
			artifact_path = str(target)
			rendered_output = _summarize_large_text(full_text)
		header = [
		 f"{event.tool_name or 'Tool'} result",
		 f"interrupted={str(interrupted).lower()}",
		 f"is_image={str(is_image).lower()}",
		 f"bytes={byte_len}",
		 f"lines={line_count}",
		]
		if event.command:
			header.append(f"command={event.command}")
		if artifact_path:
			header.append(f"full output saved to {artifact_path}")
		summary = " | ".join(header)
		if rendered_output:
			summary = f"{summary}\n{rendered_output}"
		return summary, artifact_path

	def _progress_loop(self) -> None:
		while not self._progress_stop.wait(0.25):
			with self._lock:
				source = self._progress_source
				if source is None or source.source_type != "host_file":
					continue
				host_path = source.host_path
				if host_path is None:
					continue
				now = time.monotonic()
				if now - self._last_progress_poll_at < 5.0:
					continue
				self._last_progress_poll_at = now
				summary = _read_progress_summary(host_path)
				if summary is None:
					continue
				signature = json.dumps(summary, sort_keys=True, ensure_ascii=False)
				changed = signature != self._last_progress_signature
				if changed or (now - self._last_progress_emit_at) >= 30.0:
					self._last_progress_signature = signature
					self._last_progress_emit_at = now
					self._write_event(
					 DisplayEvent(
					  case_id=self._case_id,
					  kind=DisplayKind.PROGRESS,
					  panel=DisplayPanel.PROGRESS,
					  text=_format_progress_text(
					   source=source,
					   summary=summary,
					   stale_seconds=None if changed else 30,
					  ),
					  command=source.command,
					  data={
					   "source_type": source.source_type,
					   "log_path": source.log_path,
					   "bytes": summary["bytes"],
					   "lines": summary["lines"],
					   "last_modified": summary["last_modified"],
					  },
					 )
					)


_LOG_REDIRECT_RE = re.compile(r"(?:^|\s)(?:1>>|>>|1>|>)\s*(?P<path>[^\s;&|]+)")


def _detect_progress_source(
 *,
 command: str,
 workspace_path: Path,
 runtime_workspace_path: str,
) -> ProgressSource | None:
	log_path = _extract_log_redirection(command)
	if log_path is not None:
		host_path, source_type = _resolve_progress_path(
		 log_path=log_path,
		 workspace_path=workspace_path,
		 runtime_workspace_path=runtime_workspace_path,
		)
		return ProgressSource(
		 command=command,
		 log_path=log_path,
		 source_type=source_type,
		 host_path=host_path,
		 label=f"log={log_path}",
		)
	if _looks_like_long_running_command(command):
		return ProgressSource(
		 command=command,
		 log_path=None,
		 source_type="untracked",
		 label="untracked long-running command",
		)
	return None


def _extract_log_redirection(command: str) -> str | None:
	match = _LOG_REDIRECT_RE.search(command)
	if match is None:
		return None
	return match.group("path")


def _resolve_progress_path(
 *,
 log_path: str,
 workspace_path: Path,
 runtime_workspace_path: str,
) -> tuple[Path | None, str]:
	path = Path(log_path)
	if not path.is_absolute():
		return workspace_path / path, "host_file"
	if runtime_workspace_path and str(path).startswith(runtime_workspace_path.rstrip("/") + "/"):
		relative = path.relative_to(runtime_workspace_path)
		return workspace_path / relative, "host_file"
	if runtime_workspace_path and str(path) == runtime_workspace_path.rstrip("/"):
		return workspace_path, "host_file"
	return None, "container_file"


def _looks_like_long_running_command(command: str) -> bool:
	command_lower = command.lower()
	return (
	 "&" in command
	 or ("while " in command_lower and "sleep " in command_lower)
	 or "tail -f" in command_lower
	)


def _read_progress_summary(path: Path) -> dict[str, Any] | None:
	if not path.is_file():
		return None
	content = path.read_text(encoding="utf-8", errors="replace")
	lines = content.splitlines()
	return {
	 "bytes": len(content.encode("utf-8")),
	 "lines": len(lines),
	 "last_modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
	 "tail": lines[-20:],
	}


def _format_progress_text(
 *,
 source: ProgressSource,
 summary: dict[str, Any],
 stale_seconds: int | None,
) -> str:
	last_modified = _format_last_modified(str(summary["last_modified"]))
	header = [
	 f"{source.label or 'progress'} | path={source.log_path or '-'}",
	 " | ".join(
	  part
	  for part in [
	   f"size={summary['bytes']}B",
	   f"lines={summary['lines']}",
	   f"updated={last_modified}",
	   f"no new log lines for {stale_seconds}s" if stale_seconds is not None else None,
	  ]
	  if part is not None
	 ),
	 "-" * 72,
	]
	body_lines = [*header, *[_highlight_progress_line(line) for line in summary["tail"]]]
	return "\n".join(line for line in body_lines if line is not None)


def _highlight_progress_line(line: str) -> str:
	return line


def _format_last_modified(value: str) -> str:
	dt = _parse_last_modified(value)
	if dt is None:
		return value
	now = datetime.now(timezone.utc)
	seconds_ago = max(0, int((now - dt).total_seconds()))
	return f"{dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({_format_age(seconds_ago)} ago)"


def _parse_last_modified(value: str) -> datetime | None:
	if value.isdigit():
		try:
			return datetime.fromtimestamp(int(value), timezone.utc)
		except ValueError:
			return None
	try:
		dt = datetime.fromisoformat(value)
	except ValueError:
		return None
	if dt.tzinfo is None:
		return dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc)


def _format_age(seconds: int) -> str:
	if seconds < 60:
		return f"{seconds}s"
	minutes, remaining_seconds = divmod(seconds, 60)
	if minutes < 60:
		return f"{minutes}m {remaining_seconds}s"
	hours, remaining_minutes = divmod(minutes, 60)
	if hours < 24:
		return f"{hours}h {remaining_minutes}m"
	days, remaining_hours = divmod(hours, 24)
	return f"{days}d {remaining_hours}h"


def _coerce_text(value: object) -> str:
	if value is None:
		return ""
	if isinstance(value, str):
		return value
	return json.dumps(value, ensure_ascii=False, indent=2)


def _combine_output(stdout: str, stderr: str) -> str:
	parts: list[str] = []
	if stdout:
		parts.append("STDOUT:\n" + stdout)
	if stderr:
		parts.append("STDERR:\n" + stderr)
	return "\n\n".join(parts)


def _payload_without_large_text(payload: dict[str, Any]) -> dict[str, Any]:
	cleaned = dict(payload)
	for key in ("stdout", "stderr", "content"):
		value = cleaned.get(key)
		if isinstance(value, str) and len(value.encode("utf-8")) > DISPLAY_TOOL_OUTPUT_INLINE_BYTES:
			cleaned[key] = f"<omitted large {key}>"
	return cleaned


def _summarize_large_text(text: str) -> str:
	lines = text.splitlines()
	if len(lines) <= DISPLAY_TOOL_OUTPUT_HEAD_LINES + DISPLAY_TOOL_OUTPUT_TAIL_LINES:
		return text
	head = lines[:DISPLAY_TOOL_OUTPUT_HEAD_LINES]
	tail = lines[-DISPLAY_TOOL_OUTPUT_TAIL_LINES:]
	omitted = len(lines) - len(head) - len(tail)
	return "\n".join(head + [f"... ({omitted} lines omitted) ..."] + tail)


def _compact_line(event: DisplayEvent) -> str:
	timestamp = event.ts.strftime("%H:%M:%S")
	label = str(event.kind)
	return f"[{timestamp}] {label} {event.text}".strip()


def _format_event_line(event: DisplayEvent, view: LiveViewMode) -> str:
	if view == LiveViewMode.RAW:
		return json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
	return _compact_line(event)


def _styled_line(line: str, *, raw: bool) -> Text:
	if raw:
		return Text(line, style="grey62")
	if line.startswith("[") and "] " in line:
		timestamp, rest = line[1:].split("] ", 1)
		kind, _, body = rest.partition(" ")
		text = Text()
		text.append("[", style="grey46")
		text.append(timestamp, style="grey62")
		text.append("]", style="grey46")
		text.append(" ")
		text.append(kind.upper(), style=_kind_style(kind))
		if body:
			text.append(" ")
			text.append(body, style=_body_style(kind))
		return text
	return Text(line, style="white")


def _kind_style(kind: str) -> str:
	return {
	 DisplayKind.LIFECYCLE.value: "bold bright_blue",
	 DisplayKind.STATUS.value: "bold cyan",
	 DisplayKind.ASSISTANT_TEXT.value: "bold bright_cyan",
	 DisplayKind.TOOL_CALL.value: "bold yellow",
	 DisplayKind.TOOL_RESULT.value: "yellow",
	 DisplayKind.RUNNER_OUTPUT.value: "bold magenta",
	 DisplayKind.ERROR.value: "bold white on red",
	 DisplayKind.BENCHMARK_PROGRESS.value: "bold green",
	}.get(kind, "bold white")


def _body_style(kind: str) -> str:
	return {
	 DisplayKind.LIFECYCLE.value: "white",
	 DisplayKind.STATUS.value: "bright_white",
	 DisplayKind.ASSISTANT_TEXT.value: "white",
	 DisplayKind.TOOL_CALL.value: "bright_white",
	 DisplayKind.TOOL_RESULT.value: "grey78",
	 DisplayKind.RUNNER_OUTPUT.value: "grey70",
	 DisplayKind.ERROR.value: "red",
	 DisplayKind.BENCHMARK_PROGRESS.value: "green",
	}.get(kind, "white")


def _status_style(status: str) -> str:
	return {
	 "pending": "yellow",
	 "running": "cyan",
	 "success": "green",
	 "interrupted": "bright_yellow",
	 "error": "red",
	 "skipped": "grey70",
	}.get(status, "white")


def _interrupt_style(control: RunControl) -> str:
	state = control.state_label()
	return {
	 "running": "grey70",
	 "stopping": "yellow",
	 "force-stop": "bold white on red",
	}.get(state, "grey70")


def _fallback_render_event(event: DisplayEvent) -> None:
	console = Console(file=sys.stdout, highlight=False)
	style = "red" if event.is_error or event.kind == DisplayKind.ERROR else None
	console.print(_format_event_line(event, LiveViewMode.COMPACT), style=style)
