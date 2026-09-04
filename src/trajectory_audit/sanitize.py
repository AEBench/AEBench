from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

from trajectory_audit.models import FileEffects, TraceCommand

MAX_TRACE_BATCH_BYTES = 200_000
TRACE_BATCH_OVERLAP = 0.10
MAX_ARGV_BYTES = 100_000
MAX_ARG_CHARS = 4096
MAX_PATH_CHARS = 320
MAX_FILE_PATHS_PER_COMMAND = 40
MAX_TASK_CONTEXT_CHARS = 50_000

_SECRET_ASSIGNMENT = re.compile(
	r"(?i)^(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)=(.*)$"
)
_BEARER = re.compile(r"(?i)^bearer\s+\S+$")
_HIGH_ENTROPY = re.compile(r"^[A-Za-z0-9_+/=-]{32,}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(value: object, limit: int) -> tuple[str, bool]:
	text = _CONTROL.sub("?", str(value)).replace("\r", " ").replace("\n", "\\n")
	return (text, False) if len(text) <= limit else (text[: limit - 1] + "…", True)


def _redact_arg(value: object) -> tuple[str, bool]:
	text, truncated = _clean_text(value, MAX_ARG_CHARS)
	if _SECRET_ASSIGNMENT.match(text):
		return text.split("=", 1)[0] + "=<redacted>", truncated
	if _BEARER.match(text) or _HIGH_ENTROPY.match(text):
		return "<redacted>", truncated
	return text, truncated


def _paths(value: object) -> list[str]:
	if not isinstance(value, list):
		return []
	return [_clean_text(path, MAX_PATH_CHARS)[0] for path in value[:MAX_FILE_PATHS_PER_COMMAND]]


def _argv_size(argv: list[tuple[str, bool]]) -> int:
	encoded = sum(len(json.dumps(argument).encode("utf-8")) for argument, _ in argv)
	return 2 + encoded + max(0, len(argv) - 1)


def _bound_argv(argv: list[tuple[str, bool]]) -> tuple[list[tuple[str, bool]], bool]:
	"""Keep ordinary argv in full; bound pathological commands with head/tail context."""
	if _argv_size(argv) <= MAX_ARGV_BYTES:
		return argv, False
	head: list[tuple[str, bool]] = []
	head_size = 2
	for argument in argv:
		argument_size = len(json.dumps(argument[0]).encode("utf-8")) + bool(head)
		if head_size + argument_size > MAX_ARGV_BYTES * 3 // 4:
			break
		head.append(argument)
		head_size += argument_size
	tail: list[tuple[str, bool]] = []
	total_size = head_size
	for argument in reversed(argv[len(head) :]):
		argument_size = len(json.dumps(argument[0]).encode("utf-8")) + bool(head or tail)
		if total_size + argument_size > MAX_ARGV_BYTES:
			break
		tail.insert(0, argument)
		total_size += argument_size
	return [*head, *tail], True


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
	original_arg_count = len(argv)
	sanitized_argv, argv_size_truncated = _bound_argv([_redact_arg(arg) for arg in argv])

	return TraceCommand(
		command_id=_clean_text(record.get("command_id") or f"command-{index}", 128)[0],
		argv=[arg for arg, _ in sanitized_argv],
		argv_original_count=original_arg_count,
		argv_truncated=argv_size_truncated or any(truncated for _, truncated in sanitized_argv),
		cwd=_clean_text(record.get("cwd") or "", MAX_PATH_CHARS)[0],
		exit_code=record.get("exit_code") if isinstance(record.get("exit_code"), int) else None,
		signal=record.get("signal") if isinstance(record.get("signal"), int) else None,
		complete=record.get("complete") is not False,
		duration_ms=(
			timing.get("duration_ms") if isinstance(timing.get("duration_ms"), int) else None
		),
		parent_command_id=(
			_clean_text(record["parent_command_id"], 128)[0]
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


def sanitize_task_context(value: str) -> str:
	"""Bound artifact instructions and neutralize control characters."""
	return _clean_text(value, MAX_TASK_CONTEXT_CHARS)[0]


def _batch_payload(
	commands: list[dict[str, Any]], *, total_commands: int, overlap_commands: int
) -> dict[str, Any]:
	return {
		"schema_version": "1",
		"untrusted_runtime_data": True,
		"total_commands": total_commands,
		# Conservative placeholders keep sizing valid when real indexes are added.
		"batch_index": total_commands,
		"batch_count": total_commands,
		"overlap_commands": overlap_commands,
		"commands": commands,
	}


def _serialized_size(payload: dict[str, Any]) -> int:
	return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def batch_trace(
	commands: list[TraceCommand],
	*,
	max_bytes: int = MAX_TRACE_BATCH_BYTES,
	overlap_fraction: float = TRACE_BATCH_OVERLAP,
) -> list[dict[str, Any]]:
	"""Split a trace into deterministic byte-bounded batches without omitting commands."""
	if max_bytes <= 0:
		raise ValueError("max_bytes must be positive")
	if not 0.0 <= overlap_fraction < 1.0:
		raise ValueError("overlap_fraction must be between 0 and 1")
	base_batch_limit = max(1, int(max_bytes * (1.0 - overlap_fraction)))
	serialized_commands = [command.model_dump(mode="json") for command in commands]
	base_batches: list[list[dict[str, Any]]] = []
	current: list[dict[str, Any]] = []
	for command in serialized_commands:
		single = _batch_payload([command], total_commands=len(commands), overlap_commands=0)
		if _serialized_size(single) > max_bytes:
			raise ValueError("one normalized command exceeds the trace batch byte limit")
		candidate = [*current, command]
		payload = _batch_payload(candidate, total_commands=len(commands), overlap_commands=0)
		if current and _serialized_size(payload) > base_batch_limit:
			base_batches.append(current)
			current = [command]
		else:
			current = candidate
	if current:
		base_batches.append(current)

	batches: list[dict[str, Any]] = []
	for index, batch in enumerate(base_batches):
		overlap: list[dict[str, Any]] = []
		if index and overlap_fraction:
			previous = base_batches[index - 1]
			requested = max(1, math.ceil(len(previous) * overlap_fraction))
			for command in previous[-requested:]:
				candidate = [*overlap, command, *batch]
				payload = _batch_payload(
					candidate, total_commands=len(commands), overlap_commands=len(overlap) + 1
				)
				if _serialized_size(payload) > max_bytes:
					break
				overlap.append(command)
		payload = _batch_payload(
			[*overlap, *batch], total_commands=len(commands), overlap_commands=len(overlap)
		)
		payload["batch_index"] = index + 1
		payload["batch_count"] = len(base_batches)
		batches.append(payload)
	return batches
