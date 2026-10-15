"""Create a separate sharing copy of saved run and monitor files."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

_TIMESTAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]\s+")
_SECRET_NAME = re.compile(
	r"token|secret|password|passwd|credential|authorization|(?:api|access|private)[_-]?key", re.I
)
_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=('[^']*'|\"(?:[^\"\\]|\\.)*\"|[^\s;,)\]}]+)")
_PROVIDER_TOKEN = re.compile(
	r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b"
)
_AUTH_TOKEN = re.compile(r"\b(?:Bearer|Basic)\s+([A-Za-z0-9._~+/-]+=*)", re.I)
_PRIVATE_KEY = re.compile(
	r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]+?-----END (?:[A-Z]+ )?PRIVATE KEY-----"
)
_REDACTED = "[REDACTED]"
_TEXT_SUFFIXES = {
	".csv",
	".json",
	".jsonl",
	".log",
	".md",
	".meta",
	".py",
	".sh",
	".toml",
	".tsv",
	".txt",
	".yaml",
	".yml",
}
_NON_SECRET_SCALAR = re.compile(r"(?:true|false|null|none)", re.I)
_PATH_VALUE = re.compile(r"(?:/|\./|\.\./|~/|[A-Za-z]:[\\/])[^\s?#]+")


def redact_directory(source: Path, destination: Path, *, secret_file: Path | None = None) -> Path:
	"""Redact an explicit directory tree without following links or changing originals."""
	source = source.expanduser().resolve()
	destination = destination.expanduser().absolute()
	if not source.is_dir():
		raise ValueError("source must be an existing directory")
	if destination.exists() or destination.is_symlink():
		raise ValueError("destination already exists; choose a new directory")
	destination = destination.resolve()
	if source in destination.parents or destination in source.parents or source == destination:
		raise ValueError("source and destination must not overlap")
	secrets = {
		value
		for name, value in os.environ.items()
		if _is_secret_name(name) and _is_secret_value(value)
	}
	if secret_file is not None:
		secrets.update(secret_file.expanduser().read_text(encoding="utf-8").splitlines())
	secrets.discard("")
	destination.parent.mkdir(parents=True, exist_ok=True)
	with tempfile.TemporaryDirectory(
		prefix=".aebench-redact-", dir=destination.parent
	) as temporary:
		staging = Path(temporary) / "copy"
		staging.mkdir()
		for root, directories, filenames in os.walk(
			source, followlinks=False, onerror=_raise_walk_error
		):
			relative = Path(root).relative_to(source)
			(staging / relative).mkdir(parents=True, exist_ok=True)
			for name in directories + filenames:
				mode = (Path(root) / name).lstat().st_mode
				if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
					raise ValueError(
						"source contains a symlink or special file; copy was not published"
					)
			for name in filenames:
				data = (Path(root) / name).read_bytes()
				text = _decode_text(data, name)
				(staging / relative / name).write_bytes(data)
				_discover_text(text, secrets)
		# Discover across the frozen copy before replacing any values reused in other files.
		pattern = re.compile(
			"|".join(re.escape(value) for value in sorted(secrets, key=len, reverse=True))
			or r"(?!)"
		)
		for path in staging.rglob("*"):
			if path.is_file():
				text = _decode_text(path.read_bytes(), path.name)
				path.write_bytes(_redact_text(text, pattern).encode("utf-8"))
		if destination.exists() or destination.is_symlink():
			raise ValueError("destination already exists; choose a new directory")
		staging.rename(destination)
	return destination


def _raise_walk_error(error: OSError) -> None:
	raise error


def _decode_text(data: bytes, name: str) -> str:
	if Path(name).suffix.lower() not in _TEXT_SUFFIXES or b"\x00" in data:
		raise ValueError(
			f"source contains a binary or unsupported file: {name}; copy was not published"
		)
	try:
		return data.decode("utf-8")
	except UnicodeDecodeError as exc:
		raise ValueError(
			f"source contains a non-UTF-8 file: {name}; copy was not published"
		) from exc


def _is_secret_name(name: str) -> bool:
	return bool(_SECRET_NAME.search(name)) and not name.lower().endswith(
		("_file", "_path", "_count", "_limit", "_budget", "_tokens")
	)


def _is_secret_value(value: str) -> bool:
	return bool(value) and not (_NON_SECRET_SCALAR.fullmatch(value) or _PATH_VALUE.fullmatch(value))


def _json_parts(text: str) -> tuple[str, Any] | None:
	match = _TIMESTAMP.match(text)
	prefix = match.group() if match else ""
	try:
		value = json.loads(text[len(prefix) :], object_pairs_hook=_unique_json_object)
		return (prefix, value) if isinstance(value, (dict, list, str)) else None
	except json.JSONDecodeError:
		return None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
	result: dict[str, Any] = {}
	for name, value in pairs:
		if name in result:
			raise ValueError("source contains duplicate JSON keys; copy was not published")
		result[name] = value
	return result


def _discover_text(text: str, secrets: set[str]) -> None:
	parsed = _json_parts(text)
	if parsed is not None:
		_discover_value(parsed[1], secrets)
		return
	secrets.update(_PRIVATE_KEY.findall(text))
	for line in text.splitlines():
		parsed = _json_parts(line)
		if parsed is not None:
			_discover_value(parsed[1], secrets)
			continue
		line = _TIMESTAMP.sub("", line)
		matches = list(_ASSIGNMENT.finditer(line))
		if match := _ENV_LINE.match(line):
			if not matches or any(char.isspace() for char in match.group(2)):
				matches.append(match)
		for match in matches:
			name, value = match.groups()
			if _is_secret_name(name) and value:
				if value[0] == value[-1] and value[0] in "\"'":
					if _is_secret_value(value[1:-1]):
						secrets.add(value[1:-1])
					try:
						value = json.loads(value) if value[0] == '"' else value[1:-1]
					except json.JSONDecodeError:
						pass
				if _is_secret_value(value):
					secrets.add(value)
		secrets.update(_PROVIDER_TOKEN.findall(line))
		secrets.update(_AUTH_TOKEN.findall(line))


def _discover_value(value: Any, secrets: set[str]) -> None:
	if isinstance(value, dict):
		for name, item in value.items():
			_discover_text(name, secrets)
			if _is_secret_name(name) and isinstance(item, str) and _is_secret_value(item):
				secrets.add(item)
			_discover_value(item, secrets)
	elif isinstance(value, list):
		for item in value:
			_discover_value(item, secrets)
	elif isinstance(value, str):
		# Tool output may contain another JSON document encoded as a string.
		_discover_text(value, secrets)


def _redact_value(value: Any, pattern: re.Pattern[str]) -> Any:
	if isinstance(value, dict):
		result: dict[str, Any] = {}
		for name, item in value.items():
			name = pattern.sub(_REDACTED, name)
			if name in result:
				raise ValueError("redaction would merge JSON keys; copy was not published")
			result[name] = _redact_value(item, pattern)
		return result
	if isinstance(value, list):
		return [_redact_value(item, pattern) for item in value]
	if isinstance(value, str):
		if pattern.fullmatch(value):
			return _REDACTED
		if "\n" in value:
			return _redact_text(value, pattern)
		if value.startswith(("{", "[", '"')) or _TIMESTAMP.match(value):
			if parsed := _json_parts(value):
				return _redact_json(value, parsed, pattern)
		return pattern.sub(_REDACTED, value)
	return value


def _redact_text(text: str, pattern: re.Pattern[str]) -> str:
	parsed = _json_parts(text)
	if parsed is not None:
		return _redact_json(text, parsed, pattern)
	result = []
	plain: list[str] = []
	for line in text.splitlines(keepends=True):
		if parsed := _json_parts(line):
			result.append(pattern.sub(_REDACTED, "".join(plain)))
			plain.clear()
			result.append(_redact_json(line, parsed, pattern))
		else:
			plain.append(line)
	result.append(pattern.sub(_REDACTED, "".join(plain)))
	return "".join(result)


def _redact_json(text: str, parsed: tuple[str, Any], pattern: re.Pattern[str]) -> str:
	prefix, value = parsed
	redacted = _redact_value(value, pattern)
	if redacted == value:
		return text
	ending = "\r\n" if text.endswith("\r\n") else "\n" if text.endswith("\n") else ""
	return prefix + json.dumps(redacted, ensure_ascii=True) + ending
