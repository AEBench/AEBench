from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Iterable

from trajectory_audit.models import FileEffects, TraceCommand

MAX_COMMANDS = 240
MAX_ARG_CHARS = 512
MAX_PATH_CHARS = 320
MAX_FILE_PATHS_PER_COMMAND = 40

_SECRET_ASSIGNMENT = re.compile(
	r"(?i)^(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)=(.*)$"
)
_BEARER = re.compile(r"(?i)^bearer\s+\S+$")
_HIGH_ENTROPY = re.compile(r"^[A-Za-z0-9_+/=-]{32,}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(value: object, limit: int) -> str:
	text = _CONTROL.sub("?", str(value)).replace("\r", " ").replace("\n", "\\n")
	return text if len(text) <= limit else text[: limit - 1] + "…"


def _redact_arg(value: object) -> str:
	text = _clean_text(value, MAX_ARG_CHARS)
	if _SECRET_ASSIGNMENT.match(text):
		return text.split("=", 1)[0] + "=<redacted>"
	if _BEARER.match(text) or _HIGH_ENTROPY.match(text):
		return "<redacted>"
	return text


def _paths(value: object) -> list[str]:
	if not isinstance(value, list):
		return []
	return [_clean_text(path, MAX_PATH_CHARS) for path in value[:MAX_FILE_PATHS_PER_COMMAND]]


def normalize_record(record: dict[str, Any], index: int) -> TraceCommand:
	monitors: dict[str, Any] = (
		record["monitors"] if isinstance(record.get("monitors"), dict) else {}
	)
	timing: dict[str, Any] = monitors["timing"] if isinstance(monitors.get("timing"), dict) else {}
	files: dict[str, Any] = (
		monitors["file_snapshot"] if isinstance(monitors.get("file_snapshot"), dict) else {}
	)
	# Newer brokers may promote these stable summaries to the record root.
	if isinstance(record.get("file_effects"), dict):
		files = record["file_effects"]

	argv = record.get("argv")
	if not isinstance(argv, list) or not argv:
		argv = ["<missing-argv>"]

	return TraceCommand(
		command_id=_clean_text(record.get("command_id") or f"command-{index}", 128),
		argv=[_redact_arg(arg) for arg in argv[:128]],
		cwd=_clean_text(record.get("cwd") or "", MAX_PATH_CHARS),
		exit_code=record.get("exit_code") if isinstance(record.get("exit_code"), int) else None,
		signal=record.get("signal") if isinstance(record.get("signal"), int) else None,
		complete=record.get("complete") is not False,
		duration_ms=(
			timing.get("duration_ms") if isinstance(timing.get("duration_ms"), int) else None
		),
		parent_command_id=(
			_clean_text(record["parent_command_id"], 128)
			if record.get("parent_command_id") is not None
			else None
		),
		file_effects=FileEffects(
			created=_paths(files.get("created")),
			modified=_paths(files.get("modified")),
			deleted=_paths(files.get("deleted")),
			total_created=_nonnegative_int(files.get("total_created")),
			total_modified=_nonnegative_int(files.get("total_modified")),
			total_deleted=_nonnegative_int(files.get("total_deleted")),
			truncated=bool(files.get("truncated")),
			integrity_mismatches=_paths(files.get("integrity_mismatches")),
		),
	)


def _nonnegative_int(value: object) -> int:
	return value if isinstance(value, int) and value >= 0 else 0


def normalize_trace(records: Iterable[dict[str, Any]]) -> list[TraceCommand]:
	return [normalize_record(record, index) for index, record in enumerate(records)]


def _signature(command: TraceCommand) -> str:
	executable = PurePosixPath(command.argv[0]).name
	shape = " ".join("<arg>" if len(arg) > 48 else arg for arg in command.argv[1:4])
	return hashlib.sha256(f"{executable}\0{shape}\0{command.exit_code}".encode()).hexdigest()[:12]


def _high_information(command: TraceCommand) -> bool:
	files = command.file_effects
	return (
		not command.complete
		or command.exit_code not in (None, 0)
		or command.signal is not None
		or bool(files.created or files.modified or files.deleted or files.integrity_mismatches)
	)


def compress_trace(
	commands: list[TraceCommand], max_commands: int = MAX_COMMANDS
) -> dict[str, Any]:
	"""Bound the judge input while retaining failures and file-changing commands.

	When the trace is long, all high-information commands are kept first. Remaining
	capacity is filled by deterministic, evenly spaced samples of low-information
	commands, and aggregate signatures preserve the volume that was omitted.
	"""
	if len(commands) <= max_commands:
		selected = list(commands)
	else:
		high = [command for command in commands if _high_information(command)]
		if len(high) >= max_commands:
			selected = high[:max_commands]
		else:
			low = [command for command in commands if not _high_information(command)]
			room = max_commands - len(high)
			indexes = {min(len(low) - 1, (i * len(low)) // room) for i in range(room)}
			selected = high + [low[index] for index in sorted(indexes)]
		order = {command.command_id: index for index, command in enumerate(commands)}
		selected.sort(key=lambda command: order[command.command_id])

	selected_ids = {command.command_id for command in selected}
	omitted = [command for command in commands if command.command_id not in selected_ids]
	omitted_signatures = Counter(_signature(command) for command in omitted)

	return {
		"schema_version": "1",
		"untrusted_runtime_data": True,
		"total_commands": len(commands),
		"included_commands": len(selected),
		"omitted_commands": len(omitted),
		"omitted_signature_counts": dict(sorted(omitted_signatures.items())),
		"commands": [command.model_dump(mode="json") for command in selected],
	}
