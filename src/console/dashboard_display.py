from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from rich.console import Console

from models import LiveLayoutMode, LiveViewMode
from run_control import RunControl
from runtime.reporting import TaskPaths

from .dashboard_render import (
    _compact_line,
    _combine_output,
    _coerce_text,
    _detect_progress_source,
    _fallback_render_event,
    _format_event_line,
    _format_progress_text,
    _payload_without_large_text,
    _read_progress_summary,
    _summarize_large_text,
)
from .dashboard_state import (
    DashboardCaseRow,
    DashboardSnapshot,
    DisplayConfig,
    DisplayEvent,
    DisplayKind,
    DisplayListener,
    DisplayPanel,
    ProgressSource,
    _DISPLAY_PANELS,
    _compute_effective_layout,
    _stdout,
)
from .dashboard_widgets import DashboardWidgetMixin

logger = logging.getLogger(__name__)

_StateUpdateT = TypeVar("_StateUpdateT")


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
        self._status_lines: deque[str] = deque(maxlen=200)
        self._infra_lines: deque[str] = deque(maxlen=200)
        self._agent_lines: deque[str] = deque(maxlen=200)
        self._output_lines: deque[str] = deque(maxlen=200)
        self._progress_text = "No active long-running progress source"
        self._progress_raw = ""
        self._raw_lines: deque[str] = deque(maxlen=200)
        self._active_case: str | None = None
        self._selected_cases = selected_cases or []
        self._case_status: dict[str, str] = {case_id: "pending" for case_id in self._selected_cases}
        self._case_score: dict[str, str] = {}
        self._current_view = LiveViewMode.COMPACT if config.view == LiveViewMode.AUTO else config.view
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
        self._current_view = LiveViewMode.RAW if self._current_view == LiveViewMode.COMPACT else LiveViewMode.COMPACT
        return self._current_view

    def cycle_focus(self) -> DisplayPanel:
        current_index = _DISPLAY_PANELS.index(self._focus_panel)
        self._focus_panel = _DISPLAY_PANELS[(current_index + 1) % len(_DISPLAY_PANELS)]
        return self._focus_panel

    def toggle_help(self) -> bool:
        self._show_help = not self._show_help
        return self._show_help

    def effective_layout(self, width: int) -> LiveLayoutMode:
        return _compute_effective_layout(self._current_layout, width)

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
        interrupt_state_label = self._run_control.state_label() if self._run_control is not None else None
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

    def send(self, event: DisplayEvent) -> None:
        snapshot: DashboardSnapshot | None = None
        with self._state_lock:
            if self._closed:
                return
            self._state.apply_event(event)
            snapshot = self._state.snapshot()
        if snapshot is not None:
            self._dispatch_snapshot(snapshot)

    def toggle_view(self) -> LiveViewMode:
        return self._update_state(lambda state: state.toggle_view(), fallback=self.snapshot().current_view)

    def set_layout(self, layout: LiveLayoutMode) -> LiveLayoutMode:
        def _set_layout(state: DashboardState) -> LiveLayoutMode:
            state.set_layout(layout)
            return layout

        return self._update_state(_set_layout, fallback=self.snapshot().current_layout)

    def cycle_layout(self) -> LiveLayoutMode:
        return self._update_state(lambda state: state.cycle_layout(), fallback=self.snapshot().current_layout)

    def cycle_focus(self) -> DisplayPanel:
        return self._update_state(lambda state: state.cycle_focus(), fallback=self.snapshot().focus_panel)

    def toggle_help(self) -> bool:
        return self._update_state(lambda state: state.toggle_help(), fallback=self.snapshot().show_help)

    def request_interrupt(self) -> str | None:
        run_control = self._run_control
        if run_control is None:
            return None
        run_control.request_interrupt()
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


class DashboardDisplay(DashboardWidgetMixin, BaseDashboardDisplay):
    def __init__(
        self,
        *,
        config: DisplayConfig,
        title: str,
        selected_cases: list[str] | None = None,
        run_control: RunControl | None = None,
    ) -> None:
        super().__init__(config=config, title=title, selected_cases=selected_cases, run_control=run_control)
        self._live: object | None = None
        self._console = Console(file=_stdout(), highlight=False)
        self._size_stop = threading.Event()
        self._size_thread: threading.Thread | None = None
        self._last_size: tuple[int, int] | None = None

    def __enter__(self) -> "DashboardDisplay":
        with self._state_lock:
            self._closed = False
            self._live = self._new_live(renderable=self._render(), console=self._console)
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

    def _new_live(self, *, renderable: object, console: Console) -> object:
        from rich.live import Live

        return Live(renderable, console=console, refresh_per_second=8, transient=False, auto_refresh=False)

    def _dispatch_snapshot(self, snapshot: DashboardSnapshot) -> None:
        if not self._closed and self._live is not None:
            self._live.update(self._render(snapshot=snapshot), refresh=True)

    def _refresh(self) -> None:
        self._dispatch_snapshot(self.snapshot())


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

        return TextualDashboardDisplay(config=config, title=title, selected_cases=selected_cases, run_control=run_control)
    return DashboardDisplay(config=config, title=title, selected_cases=selected_cases, run_control=run_control)


class CaseDisplaySession:
    def __init__(
        self,
        *,
        case_id: str,
        output_dir: Path,
        task_paths: TaskPaths,
        workspace_path: Path,
        runtime_workspace_path: str,
        dashboard: BaseDashboardDisplay | None = None,
        fallback_to_console: bool = True,
    ) -> None:
        self._case_id = case_id
        self._output_dir = output_dir
        self._log_path = task_paths.log_path
        self._transcript_path = task_paths.transcript_path
        self._rendered_log_path = task_paths.rendered_log_path
        self._runner_log_path = task_paths.runner_log_path
        self._infra_log_path = task_paths.infra_log_path
        self._progress_log_path = task_paths.progress_log_path
        self._tool_output_dir = task_paths.tool_output_dir
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
        self._last_progress_sent_at = 0.0
        self._last_progress_poll_at = 0.0

    @property
    def has_dashboard(self) -> bool:
        return self._dashboard is not None

    def current_progress_source(self) -> ProgressSource | None:
        with self._lock:
            return self._progress_source

    def update_workspace_context(self, *, workspace_path: Path, runtime_workspace_path: str) -> None:
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
        self._progress_thread = threading.Thread(target=self._progress_loop, name=f"aebench-progress-{self._case_id}", daemon=True)
        self._progress_thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._progress_stop.set()
        thread = self._progress_thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("progress thread %s did not stop within timeout", thread.name)
            self._progress_thread = None

    def send(self, event: DisplayEvent) -> None:
        with self._lock:
            normalized = self._normalize_event(event)
            self._write_event(normalized)

    def _write_event(self, event: DisplayEvent) -> None:
        with self._transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
        if event.kind in {DisplayKind.START, DisplayKind.STATUS, DisplayKind.ERROR}:
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
        if event.panel in {DisplayPanel.AGENT, DisplayPanel.OUTPUT} and event.kind != DisplayKind.RUNNER_OUTPUT:
            with self._rendered_log_path.open("a", encoding="utf-8") as handle:
                handle.write(_compact_line(event) + "\n")
        if self._dashboard is not None:
            self._dashboard.send(event)
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
            return event.model_copy(update={"text": text, "artifact_path": artifact_path, "data": payload})
        return event

    def _update_progress_source(self, event: DisplayEvent) -> None:
        command = event.command or ""
        if not command:
            return
        source = _detect_progress_source(command=command, workspace_path=self._workspace_path, runtime_workspace_path=self._runtime_workspace_path)
        if source is None:
            return
        self._progress_source = source
        self._last_progress_signature = None
        self._last_progress_sent_at = 0.0
        self._last_progress_poll_at = 0.0
        if source.source_type == "untracked":
            self._write_event(
                DisplayEvent(
                    case_id=self._case_id,
                    kind=DisplayKind.PROGRESS,
                    panel=DisplayPanel.PROGRESS,
                    text=f"Long-running command has no explicit log file\nCOMMAND: {source.command}",
                    command=source.command,
                    data={"source_type": source.source_type},
                )
            )

    def _summarize_tool_result(self, event: DisplayEvent, payload: dict[str, Any]) -> tuple[str, str | None]:
        stdout = _coerce_text(payload.get("stdout"))
        stderr = _coerce_text(payload.get("stderr"))
        interrupted = bool(payload.get("interrupted"))
        is_image = bool(payload.get("isImage"))
        full_text = _combine_output(stdout, stderr)
        byte_len = len(full_text.encode("utf-8"))
        line_count = len(full_text.splitlines()) if full_text else 0
        artifact_path: str | None = None
        rendered_output = full_text
        if byte_len > 16384:
            self._tool_output_seq += 1
            filename = f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}-{(event.tool_name or 'tool').lower()}-{self._tool_output_seq}.log"
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
                prev_signature = self._last_progress_signature
                prev_emit_at = self._last_progress_sent_at

            summary = _read_progress_summary(host_path)
            if summary is None:
                continue

            signature = json.dumps(summary, sort_keys=True, ensure_ascii=False)
            changed = signature != prev_signature
            now = time.monotonic()
            if not changed and (now - prev_emit_at) < 30.0:
                continue

            with self._lock:
                self._last_progress_signature = signature
                self._last_progress_sent_at = now
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
