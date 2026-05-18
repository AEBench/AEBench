from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from constants import (
    DISPLAY_TOOL_OUTPUT_HEAD_LINES,
    DISPLAY_TOOL_OUTPUT_INLINE_BYTES,
    DISPLAY_TOOL_OUTPUT_TAIL_LINES,
)
from models import LiveLayoutMode, LiveViewMode
from run_control import RunControl
from .dashboard_state import DisplayEvent, DisplayKind, DisplayPanel, ProgressSource

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
    return "
".join(line for line in body_lines if line is not None)


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
        parts.append("STDOUT:
" + stdout)
    if stderr:
        parts.append("STDERR:
" + stderr)
    return "

".join(parts)


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
    return "
".join(head + [f"... ({omitted} lines omitted) ..."] + tail)


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
        DisplayKind.START.value: "bold bright_blue",
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
        DisplayKind.START.value: "white",
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
    sys.__stdout__ and sys.__stdout__.write(_compact_line(event) + "
")
