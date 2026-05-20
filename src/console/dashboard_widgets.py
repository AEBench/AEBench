from __future__ import annotations

import threading
import time
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from models import LiveLayoutMode, LiveViewMode
from run_control import RunControl

from .dashboard_render import _interrupt_style, _status_style, _styled_line
from .dashboard_state import DashboardCaseRow, DashboardSnapshot, DisplayPanel, _compute_panel_sizes, _stdout

_RESIZE_POLL_INTERVAL_SEC = 0.2


class DashboardWidgetMixin:
    _console: Console
    _run_control: RunControl | None
    _state_lock: threading.RLock
    _closed: bool
    _live: Any | None
    _size_stop: threading.Event
    _size_thread: threading.Thread | None
    _last_size: tuple[int, int] | None

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
        text.append("\n")
        text.append(" VIEW ", style="bold black on grey70")
        text.append(f" {current.current_view.value}", style="grey70")
        text.append("   ")
        text.append(" LAYOUT ", style="bold black on grey70")
        text.append(f" {layout_kind.value}", style="grey70")
        if layout_kind == LiveLayoutMode.SINGLE:
            text.append("   ")
            text.append(" FOCUS ", style="bold black on grey70")
            text.append(f" {current.focus_panel.value}", style="grey70")
        if run_control is not None:
            text.append("\n")
            text.append(" INTERRUPT ", style="bold black on grey70")
            text.append(f" {run_control.state_label()}", style=_interrupt_style(run_control))
        return text

    def _scoreboard_table(self, snapshot: DashboardSnapshot | None = None) -> Table:
        current = snapshot or self.snapshot()
        table = Table(show_header=True, expand=True, box=box.SIMPLE_HEAVY, padding=(0, 1))
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

    def _panel(self, body: Group | Text, *, title: str, subtitle: str, border_style: str) -> Panel:
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

    def _start_size_watcher(self) -> None:
        self._size_stop.clear()
        self._size_thread = threading.Thread(
            target=self._size_watch_loop,
            name="aebench-dashboard-size",
            daemon=True,
        )
        self._size_thread.start()

    def _stop_size_watcher(self) -> None:
        self._size_stop.set()
        thread = self._size_thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("size watcher thread did not stop within timeout")
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
        return _compute_panel_sizes(current, height=self._console.size.height, run_control=self._run_control)

