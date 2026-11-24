"""Prints a short, readable line per agent event of a run, live."""

from __future__ import annotations

import functools
import itertools
import json
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .exporter import (
	CASE_RESULT,
	RUN_RESULT,
	RUNNER_LOG,
	_environment_secrets,
	_read_last_jsonl,
	_redact,
	_trace_format,
	read_codex_events,
	split_trace_line,
	trace_reader,
)

_SHELL_WRAPPER = re.compile(r"^/bin/(?:ba)?sh -lc (['\"])(.*)\1$", re.DOTALL)
_INPUT_KEYS = ("command", "file_path", "path", "pattern", "url", "description", "prompt")
# JSON lines a live run may start with before its format shows.
_DETECT_LINES = 20
Show = Callable[[str | None, str, Any], None]


def follow_trace(run_dir: Path, *, poll_seconds: float = 0.5) -> None:
	"""Prints the run's agent log, then follows it until the case result appears."""
	run_dir = run_dir.expanduser()
	log_path = run_dir / RUNNER_LOG
	if not log_path.is_file():
		print(f"Waiting for {log_path}", file=sys.stderr, flush=True)
	while not log_path.is_file():
		time.sleep(poll_seconds)
	secrets = _environment_secrets()
	width = shutil.get_terminal_size((120, 24)).columns

	def show(timestamp: str | None, label: str, summary: Any) -> None:
		clock = timestamp[11:19] if timestamp else ""
		print(f"{clock:<8}  {label:<6} {' '.join(str(summary).split())}"[:width], flush=True)

	lines = _tail_lines(run_dir, poll_seconds)
	trace_format, first = _trace_format_of(run_dir, lines, show, secrets)
	reader = trace_reader(trace_format)
	if trace_format == "codex":
		# Show commands when they start, not only once they finish.
		reader = functools.partial(read_codex_events, started=True)
	source = _echo_plain_lines(itertools.chain(first, lines), show, secrets, every=reader is None)
	if reader is None:
		for _ in source:
			pass
		return
	for event in reader(source, secrets, {}):
		for label, summary in _event_parts(event):
			show(event.get("ts"), label, summary)


def _tail_lines(run_dir: Path, poll_seconds: float) -> Iterator[str]:
	"""Yields the log's lines as they are written, until the case result appears."""
	pending = ""
	with (run_dir / RUNNER_LOG).open(encoding="utf-8", errors="replace") as handle:
		while True:
			# Checked before reading so the last lines are never missed.
			finished = (run_dir / CASE_RESULT).is_file()
			chunk = handle.read()
			*lines, pending = (pending + chunk).split("\n")
			yield from lines + ([pending] if finished and pending else [])
			if finished:
				return
			if not chunk:
				time.sleep(poll_seconds)


def _trace_format_of(
	run_dir: Path, lines: Iterator[str], show: Show, secrets: Mapping[str, str]
) -> tuple[str, list[str]]:
	"""Returns the run's trace format and the JSON lines read to find it.

	A finished run records its format; a live one is known by its first JSON
	events, and is read as raw if none of the first few is recognized.
	"""
	if (run_dir / RUN_RESULT).is_file():
		return _trace_format(_read_last_jsonl(run_dir / RUN_RESULT)), []
	read: list[str] = []
	for line in lines:
		timestamp, text, payload = split_trace_line(line)
		if payload is None:
			if text:
				show(timestamp, "out", _redact(text, secrets))
			continue
		read.append(line)
		if trace_format := _detect_trace_format(payload):
			return trace_format, read
		if len(read) == _DETECT_LINES:
			break
	return "raw", read


def _detect_trace_format(payload: Mapping[str, Any]) -> str | None:
	event_type = str(payload.get("type") or "")
	# ZCode wraps every event's fields in a payload object.
	if isinstance(payload.get("payload"), dict):
		return "zcode"
	if event_type == "thread.started" or event_type.startswith("item."):
		return "codex"
	if event_type in {"system", "assistant", "user", "result"}:
		return "claude_code"
	if event_type in {"session", "status", "thinking", "text", "tool_call"}:
		return "dsh"
	return None


def _echo_plain_lines(
	lines: Iterable[str], show: Show, secrets: Mapping[str, str], *, every: bool
) -> Iterator[str]:
	"""Passes lines on, printing those that are not JSON (or all of them if ``every``)."""
	for line in lines:
		timestamp, text, payload = split_trace_line(line)
		if text and (every or payload is None):
			show(timestamp, "out", _redact(text, secrets))
		yield line


def _event_parts(event: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
	if event["type"] == "codex_item":
		yield from _codex_parts(event)
		return
	if event["type"] == "result":
		reason = event.get("subtype") or event.get("stop_reason") or "finished"
		turns = event.get("num_turns")
		yield "done", f"{reason}, {turns} turns" if isinstance(turns, int) else reason
	for block in event.get("blocks", []):
		if block.get("type") == "text" and event["type"] == "assistant":
			yield "text", block.get("text")
		elif block.get("type") == "tool_use":
			tool_input = block.get("input") or {}
			key = next((key for key in _INPUT_KEYS if tool_input.get(key)), None)
			yield str(block.get("name")), tool_input[key] if key else json.dumps(tool_input)
		elif block.get("type") == "tool_result" and block.get("is_error"):
			yield "error", block.get("content")


def _codex_parts(event: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
	item = event.get("item", {})
	kind, started = item.get("type"), event.get("phase") == "started"
	if kind == "command_execution":
		command = str(item.get("command"))
		match = _SHELL_WRAPPER.match(command)
		command = match.group(2) if match else command
		if started:
			yield "shell", command
		elif item.get("exit_code") not in (0, None):
			yield "error", f"exit {item['exit_code']}: {command}"
	elif kind == "agent_message" and not started:
		yield "text", item.get("text")
	elif kind == "file_change" and not started:
		yield "edit", ", ".join(str(change.get("path")) for change in item.get("changes", []))
