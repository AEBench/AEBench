from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import TextIO

from .settings import get_settings


def safe_name(value: str | None, fallback: str = "unknown") -> str:
	raw = (value or fallback).strip()
	if not raw:
		raw = fallback
	return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._").lower() or fallback


def timeout_env_dict(timeout_ms: int) -> dict[str, str]:
	return {
	 "BASH_MAX_TIMEOUT_MS": str(timeout_ms),
	 "BASH_DEFAULT_TIMEOUT_MS": str(timeout_ms),
	}


def apply_timeout_env(timeout_ms: int) -> None:
	os.environ.update(timeout_env_dict(timeout_ms))


def write_claude_settings(timeout_ms: int) -> None:
	claude_dir = Path.home() / ".claude"
	claude_dir.mkdir(exist_ok=True)
	with (claude_dir / "settings.json").open("w", encoding="utf-8") as handle:
		json.dump({"env": timeout_env_dict(timeout_ms)}, handle, indent=2)


def has_api_key() -> bool:
	return get_settings().has_api_key


def build_agent_env(timeout_ms: int) -> dict[str, str]:
	return get_settings().agent_env(timeout_ms)


def resolve_ephemeral_workspace_root() -> Path:
	return Path(get_settings().ephemeral_workspace_root).expanduser().resolve()


def preserve_failed_workspace() -> bool:
	return get_settings().preserve_failed_workspace


class Tee:
	def __init__(self, stream: TextIO, log_path: Path) -> None:
		self._stream = stream
		self._log_path = log_path
		self._file: TextIO | None = None

	def __enter__(self) -> "Tee":
		self._file = self._log_path.open("a", encoding="utf-8")
		return self

	def __exit__(self, *_args: object) -> None:
		if self._file is not None:
			self._file.close()
			self._file = None

	def write(self, data: str) -> int:
		written = self._stream.write(data)
		file = self._file
		if file is not None:
			file.write(data)
			file.flush()
		return written

	def flush(self) -> None:
		self._stream.flush()
		file = self._file
		if file is not None:
			file.flush()

	@property
	def encoding(self) -> str:
		return getattr(self._stream, "encoding", "utf-8")

	def isatty(self) -> bool:
		return bool(getattr(self._stream, "isatty", lambda: False)())

	def fileno(self) -> int:
		fileno = getattr(self._stream, "fileno", None)
		if fileno is None:
			raise io.UnsupportedOperation("underlying stream does not support fileno")
		try:
			value = fileno()
		except (AttributeError, io.UnsupportedOperation) as exc:
			raise io.UnsupportedOperation("underlying stream does not support fileno") from exc
		if not isinstance(value, int):
			raise io.UnsupportedOperation("underlying stream does not support fileno")
		return value


def send_event(listener, event) -> None:
	"""Null-safe event emit: calls listener.emit(event) if listener supports it."""
	if listener is not None and hasattr(listener, "emit"):
		listener.emit(event)
