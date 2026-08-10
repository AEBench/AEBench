"""Durable command trace: ``commands.jsonl`` plus per-command stream captures."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

from .types import CommandRecord

TRACE_BASENAME = "commands.jsonl"
CAPTURE_DIRNAME = "commands"


class CaptureSink:
	"""Writes one command's stream to disk in full.

	The file is created on first write, so a run with thousands of silent
	nested shells does not leave thousands of empty files behind.
	"""

	def __init__(self, path: Path) -> None:
		"""Initializes a sink that writes to ``path``."""
		self._path = path
		self._handle: Any = None
		self._opened = False

	@property
	def path(self) -> Path | None:
		"""Returns the capture path, or ``None`` if nothing was ever written."""
		return self._path if self._opened else None

	def write(self, data: bytes) -> None:
		"""Appends a chunk, creating the file on first use."""
		if not data:
			return
		if self._handle is None:
			self._path.parent.mkdir(parents=True, exist_ok=True)
			self._handle = self._path.open("wb")
			self._opened = True
		self._handle.write(data)

	def close(self) -> None:
		"""Closes the underlying file if it was ever opened."""
		if self._handle is not None:
			self._handle.flush()
			self._handle.close()
			self._handle = None


class CommandTraceWriter:
	"""Allocates command ids and appends records to the trace.

	Safe to share across broker threads: id allocation and record writes are
	serialized, so ids are monotonic and lines are never interleaved.
	"""

	def __init__(self, output_dir: Path) -> None:
		"""Initializes a trace under ``output_dir``."""
		self._output_dir = Path(output_dir).expanduser().resolve()
		self._capture_dir = self._output_dir / CAPTURE_DIRNAME
		self._trace_path = self._output_dir / TRACE_BASENAME
		self._lock = threading.Lock()
		self._next_id = 1
		self._handle: Any = None

	@property
	def trace_path(self) -> Path:
		"""Returns the path of the JSONL trace."""
		return self._trace_path

	@property
	def capture_dir(self) -> Path:
		"""Returns the directory holding per-command stream captures."""
		return self._capture_dir

	def allocate_command_id(self) -> str:
		"""Returns the next monotonic command id."""
		with self._lock:
			command_id = f"cmd_{self._next_id:06d}"
			self._next_id += 1
		return command_id

	def open_capture(self, command_id: str, stream: str) -> CaptureSink:
		"""Opens a lazily-created sink for one stream of one command."""
		if stream not in {"stdout", "stderr"}:
			raise ValueError(f"unknown stream: {stream!r}")
		return CaptureSink(self._capture_dir / f"{command_id}.{stream}.log")

	def write(self, record: CommandRecord) -> None:
		"""Appends one record, flushing so a killed run keeps its evidence."""
		line = json.dumps(record.to_json_dict(), separators=(",", ":"))
		with self._lock:
			if self._handle is None:
				self._output_dir.mkdir(parents=True, exist_ok=True)
				self._handle = self._trace_path.open("a", encoding="utf-8")
			self._handle.write(line)
			self._handle.write("\n")
			self._handle.flush()

	def relative_capture_path(self, path: Path | None) -> str | None:
		"""Returns a capture path relative to the output dir, when possible."""
		if path is None:
			return None
		try:
			return str(path.relative_to(self._output_dir))
		except ValueError:
			return str(path)

	def close(self) -> None:
		"""Closes the trace file."""
		with self._lock:
			if self._handle is not None:
				self._handle.flush()
				self._handle.close()
				self._handle = None

	def __enter__(self) -> "CommandTraceWriter":
		"""Enters a context that closes the trace on exit."""
		return self

	def __exit__(self, *_exc: object) -> None:
		"""Closes the trace."""
		self.close()


def read_command_trace(output_dir: Path) -> list[dict[str, Any]]:
	"""Reads a trace written by :class:`CommandTraceWriter`."""
	return list(iter_command_trace(output_dir))


def iter_command_trace(output_dir: Path) -> Iterator[dict[str, Any]]:
	"""Yields trace records in order, skipping a truncated trailing line."""
	trace_path = Path(output_dir).expanduser().resolve() / TRACE_BASENAME
	if not trace_path.is_file():
		return
	with trace_path.open("r", encoding="utf-8") as handle:
		for line in handle:
			line = line.strip()
			if not line:
				continue
			try:
				record = json.loads(line)
			except json.JSONDecodeError:
				continue
			if isinstance(record, dict):
				yield record


__all__ = [
	"CAPTURE_DIRNAME",
	"TRACE_BASENAME",
	"CaptureSink",
	"CommandTraceWriter",
	"iter_command_trace",
	"read_command_trace",
]
