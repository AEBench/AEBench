"""Prints a short, readable line per agent event of a run, live."""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from .exporter import (
	CASE_RESULT,
	RUNNER_LOG,
	_claude_event,
	_codex_event,
	_environment_secrets,
	_redact,
	split_trace_line,
)

_SHELL_WRAPPER = re.compile(r"^/bin/(?:ba)?sh -lc (['\"])(.*)\1$", re.DOTALL)
_INPUT_KEYS = ("command", "file_path", "path", "pattern", "url", "description", "prompt")


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
	pending = ""
	with log_path.open(encoding="utf-8", errors="replace") as handle:
		while True:
			# Checked before reading so the last lines are never missed.
			finished = (run_dir / CASE_RESULT).is_file()
			chunk = handle.read()
			*lines, pending = (pending + chunk).split("\n")
			for line in lines + ([pending] if finished else []):
				for text in format_trace_line(line, secrets):
					print(text[:width], flush=True)
			if finished:
				return
			if not chunk:
				time.sleep(poll_seconds)


def format_trace_line(raw_line: str, secrets: Mapping[str, str]) -> list[str]:
	"""Returns one `HH:MM:SS  label  summary` line per meaningful event in a log line."""
	timestamp, text, payload = split_trace_line(raw_line)
	if payload is None:
		parts = [("out", _redact(text, secrets))] if text else []
	elif payload.get("type") in {"assistant", "user", "result"}:
		event = _claude_event(payload, timestamp, "default", 0, secrets)
		parts = list(_claude_parts(event))
	elif payload.get("type") in {"item.started", "item.completed"}:
		event = _codex_event(payload, timestamp, "default", secrets)
		parts = list(_codex_parts(event))
	else:
		parts = []
	clock = timestamp[11:19] if timestamp else ""
	return [f"{clock:<8}  {label:<6} {' '.join(str(summary).split())}" for label, summary in parts]


def _claude_parts(event: dict[str, Any]) -> Iterator[tuple[str, Any]]:
	if event["type"] == "result":
		yield "done", f"{event['subtype']}, {event['num_turns']} turns"
	for block in event.get("blocks", []):
		if block.get("type") == "text" and event["type"] == "assistant":
			yield "text", block.get("text")
		elif block.get("type") == "tool_use":
			tool_input = block.get("input") or {}
			key = next((key for key in _INPUT_KEYS if tool_input.get(key)), None)
			yield str(block.get("name")), tool_input[key] if key else json.dumps(tool_input)
		elif block.get("type") == "tool_result" and block.get("is_error"):
			yield "error", block.get("content")


def _codex_parts(event: dict[str, Any]) -> Iterator[tuple[str, Any]]:
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
