"""Textual TUI backend for live dashboard."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from queue import Empty, Queue
from typing import ClassVar, TypeVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, Footer, RichLog, Static

from models import LiveLayoutMode
from run_control import RunControl

from .dashboard import BaseDashboardDisplay, DashboardSnapshot, DisplayConfig, DisplayPanel
from .dashboard_state import (
	_compute_effective_layout,
	_compute_panel_sizes,
)

logger = logging.getLogger(__name__)

_HELP_TEXT = "\n".join(
	[
		"v  toggle compact/raw",
		"1/2/3  set single/split/triple layout",
		"l  cycle layout",
		"q  interrupt; press again to force stop",
		"?  toggle help",
	]
)

_T = TypeVar("_T")


def _panel_lines(snapshot: DashboardSnapshot, panel: DisplayPanel) -> tuple[str, ...]:
	if panel == DisplayPanel.STATUS:
		return _status_lines(snapshot)
	if panel == DisplayPanel.INFRA:
		return snapshot.raw_lines if snapshot.current_view.value == "raw" else snapshot.infra_lines
	if panel == DisplayPanel.AGENT:
		return snapshot.raw_lines if snapshot.current_view.value == "raw" else snapshot.agent_lines
	if panel == DisplayPanel.PROGRESS:
		body = (
			snapshot.progress_raw
			if snapshot.current_view.value == "raw"
			else snapshot.progress_text
		)
		return tuple(body.splitlines()) if body else ("No active long-running progress source",)
	return snapshot.raw_lines if snapshot.current_view.value == "raw" else snapshot.output_lines


def _status_lines(snapshot: DashboardSnapshot) -> tuple[str, ...]:
	lines = [f"AE  {snapshot.title}"]
	if snapshot.active_case is not None:
		lines.append(f"ACTIVE  {snapshot.active_case}")
	layout_text = snapshot.current_layout.value
	if snapshot.current_layout == LiveLayoutMode.AUTO:
		layout_text = "auto"
	lines.append(f"VIEW  {snapshot.current_view.value}    LAYOUT  {layout_text}")
	if snapshot.interrupt_state_label is not None:
		lines.append(f"INTERRUPT  {snapshot.interrupt_state_label}")
	if snapshot.case_rows:
		lines.append("")
		lines.append("CASE                          STATUS       SCORE")
		for row in snapshot.case_rows:
			marker = "*" if row.is_active else " "
			lines.append(f"{marker} {row.case_id:<28} {row.status:<12} {row.score:>8}")
	if snapshot.status_lines:
		lines.append("")
		lines.extend(snapshot.status_lines)
	return tuple(lines)


def _panel_title(panel: DisplayPanel) -> str:
	return {
		DisplayPanel.STATUS: "STATUS",
		DisplayPanel.INFRA: "INFRA",
		DisplayPanel.AGENT: "AGENT",
		DisplayPanel.OUTPUT: "COMMAND / OUTPUT",
		DisplayPanel.PROGRESS: "PROGRESS",
	}[panel]


def _panel_border_color(panel: DisplayPanel) -> str:
	return {
		DisplayPanel.STATUS: "ansi_bright_blue",
		DisplayPanel.INFRA: "magenta",
		DisplayPanel.AGENT: "ansi_bright_cyan",
		DisplayPanel.OUTPUT: "yellow",
		DisplayPanel.PROGRESS: "ansi_bright_green",
	}[panel]


def _panel_subtitle(snapshot: DashboardSnapshot, panel: DisplayPanel) -> str:
	if panel == DisplayPanel.STATUS:
		active = snapshot.active_case or "-"
		return f"active={active}  cases={len(snapshot.case_rows)}"
	return {
		DisplayPanel.INFRA: "runtime / third-party",
		DisplayPanel.AGENT: "assistant transcript",
		DisplayPanel.OUTPUT: "tool calls and results",
		DisplayPanel.PROGRESS: "long-running log summary",
	}[panel]


def _single_main_panel(snapshot: DashboardSnapshot) -> DisplayPanel:
	return snapshot.focus_panel


def _visible_focus_id(layout: LiveLayoutMode, panel: DisplayPanel) -> str:
	if layout == LiveLayoutMode.SINGLE:
		return "#single-main"
	if layout == LiveLayoutMode.SPLIT:
		if panel == DisplayPanel.OUTPUT:
			return "#split-output"
		if panel == DisplayPanel.PROGRESS:
			return "#split-progress"
		return "#split-agent"
	if panel == DisplayPanel.STATUS:
		return "#triple-status"
	if panel == DisplayPanel.INFRA:
		return "#triple-infra"
	if panel == DisplayPanel.OUTPUT:
		return "#triple-output"
	if panel == DisplayPanel.PROGRESS:
		return "#triple-progress"
	return "#triple-agent"


def _log_content(widget: RichLog, lines: tuple[str, ...]) -> None:
	widget.clear()
	if lines:
		widget.write("\n".join(lines), scroll_end=True)


def _pane_log(*, pane_id: str, classes: str, wrap: bool) -> RichLog:
	return RichLog(
		id=pane_id,
		classes=classes,
		wrap=wrap,
		auto_scroll=True,
		highlight=False,
		markup=False,
	)


class _DashboardTextualApp(App[None]):
	CSS = """
    Screen {
        layout: vertical;
    }

    #toolbar {
        height: auto;
        padding: 0 1;
        border: round $panel;
        background: $surface;
    }

    #toolbar-title {
        width: 1fr;
        content-align: left middle;
        padding: 0 1 0 0;
    }

    .toolbar-button {
        margin: 0 1 0 0;
        min-width: 11;
    }

    ContentSwitcher {
        height: 1fr;
    }

    .layout-root {
        height: 1fr;
    }

    .pane {
        border: round $panel;
        height: 1fr;
    }

    .status-pane {
        height: 12;
    }

    .progress-pane {
        height: 8;
    }

    #layout-triple-main {
        width: 4fr;
        height: 1fr;
    }

    #layout-triple-body {
        height: 1fr;
    }

    #layout-triple-left {
        width: 1fr;
        height: 1fr;
    }

    #split-agent, #split-output, #triple-agent, #triple-output {
        width: 1fr;
    }

    #help-overlay {
        layer: overlay;
        dock: bottom;
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
        display: none;
    }
    """

	BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
		Binding("v", "toggle_view", "View"),
		Binding("1", "layout_single", "Single"),
		Binding("2", "layout_split", "Split"),
		Binding("3", "layout_triple", "Triple"),
		Binding("l", "cycle_layout", "Cycle"),
		Binding("q", "interrupt", "Interrupt"),
		Binding("question_mark", "toggle_help", "Help"),
	]

	def __init__(
		self,
		*,
		display: "TextualDashboardDisplay",
		initial_snapshot: DashboardSnapshot,
		update_queue: Queue[DashboardSnapshot],
		ready: threading.Event,
		run_control: RunControl | None,
	) -> None:
		super().__init__()
		self._display_controller = display
		self._snapshot = initial_snapshot
		self._queue = update_queue
		self._ready_event = ready
		self._run_control = run_control

	def compose(self) -> ComposeResult:
		with Horizontal(id="toolbar"):
			yield Static(id="toolbar-title")
			yield Button("Compact", id="btn-view", classes="toolbar-button", variant="primary")
			yield Button(
				"1 Col", id="btn-layout-single", classes="toolbar-button", variant="default"
			)
			yield Button(
				"2 Col", id="btn-layout-split", classes="toolbar-button", variant="default"
			)
			yield Button(
				"3 Col", id="btn-layout-triple", classes="toolbar-button", variant="default"
			)
			yield Button("Help", id="btn-help", classes="toolbar-button", variant="warning")
			yield Button("Interrupt", id="btn-stop", classes="toolbar-button", variant="error")
		with ContentSwitcher(id="layout-switcher", initial="layout-single"):
			with Vertical(id="layout-single", classes="layout-root"):
				yield _pane_log(pane_id="single-status", classes="pane status-pane", wrap=False)
				yield _pane_log(pane_id="single-main", classes="pane", wrap=True)
				yield _pane_log(pane_id="single-progress", classes="pane progress-pane", wrap=True)
			with Vertical(id="layout-split", classes="layout-root"):
				yield _pane_log(pane_id="split-status", classes="pane status-pane", wrap=False)
				with Horizontal(id="layout-split-body"):
					yield _pane_log(pane_id="split-agent", classes="pane", wrap=True)
					yield _pane_log(pane_id="split-output", classes="pane", wrap=True)
				yield _pane_log(pane_id="split-progress", classes="pane progress-pane", wrap=True)
			with Horizontal(id="layout-triple", classes="layout-root"):
				with Vertical(id="layout-triple-left"):
					yield _pane_log(pane_id="triple-status", classes="pane status-pane", wrap=False)
					yield _pane_log(pane_id="triple-infra", classes="pane", wrap=True)
				with Vertical(id="layout-triple-main"):
					with Horizontal(id="layout-triple-body"):
						yield _pane_log(pane_id="triple-agent", classes="pane", wrap=True)
						yield _pane_log(pane_id="triple-output", classes="pane", wrap=True)
					yield _pane_log(
						pane_id="triple-progress",
						classes="pane progress-pane",
						wrap=True,
					)
		yield Static(_HELP_TEXT, id="help-overlay")
		yield Footer()

	def on_mount(self) -> None:
		self.set_interval(0.1, self._drain_updates)
		self._apply_snapshot(self._snapshot)
		self._ready_event.set()
		if self._display_controller.consume_exit_request():
			self.call_after_refresh(self.exit)

	def on_resize(self) -> None:
		self._apply_snapshot(self._snapshot)

	def on_button_pressed(self, event: Button.Pressed) -> None:
		button_id = event.button.id
		if button_id == "btn-view":
			self.action_toggle_view()
		elif button_id == "btn-layout-single":
			self.action_layout_single()
		elif button_id == "btn-layout-split":
			self.action_layout_split()
		elif button_id == "btn-layout-triple":
			self.action_layout_triple()
		elif button_id == "btn-help":
			self.action_toggle_help()
		elif button_id == "btn-stop":
			self.action_interrupt()

	def action_toggle_view(self) -> None:
		self._display_controller.toggle_view()

	def action_layout_single(self) -> None:
		self._display_controller.set_layout(LiveLayoutMode.SINGLE)

	def action_layout_split(self) -> None:
		self._display_controller.set_layout(LiveLayoutMode.SPLIT)

	def action_layout_triple(self) -> None:
		self._display_controller.set_layout(LiveLayoutMode.TRIPLE)

	def action_cycle_layout(self) -> None:
		self._display_controller.cycle_layout()

	def action_cycle_focus(self) -> None:
		self._display_controller.cycle_focus()

	def action_toggle_help(self) -> None:
		self._display_controller.toggle_help()

	def action_interrupt(self) -> None:
		if self._run_control is None:
			return
		self._display_controller.request_interrupt()

	def _drain_updates(self) -> None:
		latest: DashboardSnapshot | None = None
		while True:
			try:
				latest = self._queue.get_nowait()
			except Empty:
				break
		if latest is not None:
			self._apply_snapshot(latest)

	def _apply_snapshot(self, snapshot: DashboardSnapshot) -> None:
		self._snapshot = snapshot
		layout = _compute_effective_layout(snapshot.current_layout, self.size.width)
		status_size, progress_size = _compute_panel_sizes(
			snapshot,
			height=self.size.height,
			run_control=self._run_control,
		)
		switcher = self.query_one("#layout-switcher", ContentSwitcher)
		switcher.current = {
			LiveLayoutMode.SINGLE: "layout-single",
			LiveLayoutMode.SPLIT: "layout-split",
			LiveLayoutMode.TRIPLE: "layout-triple",
		}[layout]
		self.query_one("#help-overlay", Static).display = snapshot.show_help
		self.query_one("#help-overlay", Static).update(_HELP_TEXT)
		self._update_toolbar(snapshot, layout)
		self._apply_panel_sizes(status_size=status_size, progress_size=progress_size)
		self._update_single(snapshot)
		self._update_split(snapshot)
		self._update_triple(snapshot)
		self.call_after_refresh(
			lambda: self.query_one(_visible_focus_id(layout, snapshot.focus_panel)).focus()
		)

	def _update_toolbar(
		self,
		snapshot: DashboardSnapshot,
		layout: LiveLayoutMode,
	) -> None:
		active_case = snapshot.active_case or "-"
		interrupt = snapshot.interrupt_state_label or "running"
		self.query_one("#toolbar-title", Static).update(
			f"{snapshot.title}  active={active_case}  view={snapshot.current_view.value}  "
			f"layout={layout.value}  stop={interrupt}"
		)
		view_button = self.query_one("#btn-view", Button)
		view_button.label = "Raw" if snapshot.current_view.value == "raw" else "Compact"
		view_button.variant = "primary"
		for selector, selected_layout in (
			("#btn-layout-single", LiveLayoutMode.SINGLE),
			("#btn-layout-split", LiveLayoutMode.SPLIT),
			("#btn-layout-triple", LiveLayoutMode.TRIPLE),
		):
			button = self.query_one(selector, Button)
			button.variant = "success" if layout == selected_layout else "default"
		help_button = self.query_one("#btn-help", Button)
		help_button.label = "Help On" if snapshot.show_help else "Help"
		help_button.variant = "warning" if snapshot.show_help else "default"
		stop_button = self.query_one("#btn-stop", Button)
		stop_button.label = (
			"Force Stop"
			if interrupt == "stopping"
			else "Stopped"
			if interrupt == "force-stop"
			else "Interrupt"
		)
		stop_button.variant = "error" if interrupt != "force-stop" else "default"

	def _apply_panel_sizes(self, *, status_size: int, progress_size: int) -> None:
		for selector in ("#single-status", "#split-status", "#triple-status"):
			self.query_one(selector, RichLog).styles.height = status_size
		for selector in ("#single-progress", "#split-progress", "#triple-progress"):
			self.query_one(selector, RichLog).styles.height = progress_size

	def _update_single(self, snapshot: DashboardSnapshot) -> None:
		self._update_log("#single-status", snapshot, DisplayPanel.STATUS)
		self._update_log("#single-main", snapshot, _single_main_panel(snapshot))
		self._update_log("#single-progress", snapshot, DisplayPanel.PROGRESS)

	def _update_split(self, snapshot: DashboardSnapshot) -> None:
		self._update_log("#split-status", snapshot, DisplayPanel.STATUS)
		self._update_log("#split-agent", snapshot, DisplayPanel.AGENT)
		self._update_log("#split-output", snapshot, DisplayPanel.OUTPUT)
		self._update_log("#split-progress", snapshot, DisplayPanel.PROGRESS)

	def _update_triple(self, snapshot: DashboardSnapshot) -> None:
		self._update_log("#triple-status", snapshot, DisplayPanel.STATUS)
		self._update_log("#triple-infra", snapshot, DisplayPanel.INFRA)
		self._update_log("#triple-agent", snapshot, DisplayPanel.AGENT)
		self._update_log("#triple-output", snapshot, DisplayPanel.OUTPUT)
		self._update_log("#triple-progress", snapshot, DisplayPanel.PROGRESS)

	def _update_log(self, selector: str, snapshot: DashboardSnapshot, panel: DisplayPanel) -> None:
		widget = self.query_one(selector, RichLog)
		widget.border_title = _panel_title(panel)
		widget.border_subtitle = _panel_subtitle(snapshot, panel)
		widget.styles.border = ("round", _panel_border_color(panel))
		_log_content(widget, _panel_lines(snapshot, panel))


class TextualDashboardDisplay(BaseDashboardDisplay):
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
		self._app: _DashboardTextualApp | None = None
		self._ready = threading.Event()
		self._updates: Queue[DashboardSnapshot] = Queue()
		self._close_requested = threading.Event()

	def __enter__(self) -> "TextualDashboardDisplay":
		with self._state_lock:
			self._closed = False
		self._prepare_app_state()
		return self

	def close(self) -> None:
		with self._state_lock:
			if self._closed:
				return
			self._closed = True
		self._request_app_exit()

	def _dispatch_snapshot(self, snapshot: DashboardSnapshot) -> None:
		if not self._closed:
			self._updates.put(snapshot)

	def run_worker(self, worker: Callable[[], _T]) -> _T:
		with self._state_lock:
			self._closed = False
		self._prepare_app_state()
		result_box: dict[str, _T] = {}
		error_box: dict[str, BaseException] = {}

		def _worker_main() -> None:
			try:
				result_box["value"] = worker()
			except BaseException as exc:  # pragma: no cover - re-raised on caller thread
				error_box["error"] = exc
			finally:
				self._request_app_exit()

		worker_thread = threading.Thread(
			target=_worker_main,
			name="aebench-textual-worker",
			daemon=True,
		)
		worker_thread.start()
		try:
			self._run_app()
		finally:
			worker_thread.join(timeout=5.0)
			if worker_thread.is_alive():
				logger.warning("worker thread did not finish within join timeout")
			self.close()
		if "error" in error_box:
			raise error_box["error"]
		return result_box["value"]

	def consume_exit_request(self) -> bool:
		if not self._close_requested.is_set():
			return False
		self._close_requested.clear()
		return True

	def _prepare_app_state(self) -> None:
		with self._state_lock:
			self._app = None
		self._ready.clear()
		self._close_requested.clear()
		self._updates = Queue()
		self._updates.put(self.snapshot())

	def _request_app_exit(self) -> None:
		self._close_requested.set()
		with self._state_lock:
			app = self._app
		if app is None or app._loop is None:
			return
		try:
			if getattr(app, "_thread_id", 0) == threading.get_ident():
				app.exit()
			else:
				app.call_from_thread(app.exit)
		except RuntimeError:
			logger.debug("app exit raised RuntimeError (event loop already closed)")

	def _run_app(self) -> None:
		app = _DashboardTextualApp(
			display=self,
			initial_snapshot=self.snapshot(),
			update_queue=self._updates,
			ready=self._ready,
			run_control=self._run_control,
		)
		with self._state_lock:
			self._app = app
		try:
			app.run()
		finally:
			self._ready.set()
