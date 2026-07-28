"""Shared parsing and provenance helpers for the Paralegal case."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
	import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
	import tomli as tomllib

WRAPPER_COMMIT = "d26799fb0f4b0d2bc2cf7b6ad0e1b6afc732b9b4"
CODEQL_VERSION = "2.19.3"
CODEQL_COMMIT = "6a0341d3c50cf3caf90c2fc8dde3b364e2422954"
DOCKER_IMAGE = "paralegal:osdi25-artifact"
PARALEGAL_COMMIT = "5e6e565d566eddccae61c4a81f396f3c8e261b77"
PARALEGAL_BENCH_COMMIT = "ff1c2a6ae4e54a78d21a7cd6a6be1f35e119cbe1"

_CODEQL_ROOT_RE = re.compile(r"file:///.*?/codeql-experimentation/")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REQUIRED_SMOKE_COLUMNS = frozenset(
	{
		"id",
		"experiment",
		"application",
		"expectation",
		"result",
		"pdg_timed_out",
		"analyzer_time",
		"last_self_time",
		"policy_time",
		"pdg_functions",
		"pdg_locs",
		"seen_functions",
		"seen_locs",
		"file_size",
	}
)
_POSITIVE_SMOKE_COLUMNS = (
	"analyzer_time",
	"last_self_time",
	"policy_time",
	"pdg_functions",
	"pdg_locs",
	"seen_functions",
	"seen_locs",
	"file_size",
)
_REQUIRED_CONTROLLER_COLUMNS = frozenset(
	{
		"run_id",
		"name",
		"num_nodes",
		"num_edges",
		"unique_locs",
		"unique_functions",
		"analyzed_locs",
		"analyzed_functions",
	}
)
_POSITIVE_CONTROLLER_COLUMNS = (
	"num_nodes",
	"num_edges",
	"unique_locs",
	"unique_functions",
	"analyzed_locs",
	"analyzed_functions",
)


@dataclass(frozen=True, slots=True)
class ProcessOutput:
	returncode: int
	stdout: str
	stderr: str

	@property
	def ok(self) -> bool:
		return self.returncode == 0

	@property
	def combined(self) -> str:
		return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()


@dataclass(frozen=True, slots=True)
class CodeQLTable:
	header: tuple[str, ...]
	rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class ExpectedOutputEntry:
	expected_path: str
	result_path: str
	sha256: str
	bytes: int
	rows: int


@dataclass(frozen=True, slots=True)
class SmokeResults:
	rows: tuple[dict[str, str], ...]
	run_ids: frozenset[int]


def run_process(
	cmd: tuple[str, ...],
	*,
	cwd: Path | None = None,
	timeout_seconds: float = 60.0,
) -> ProcessOutput:
	"""Run a bounded diagnostic command without raising for normal failures."""
	try:
		result = subprocess.run(
			cmd,
			cwd=cwd,
			capture_output=True,
			text=True,
			timeout=timeout_seconds,
			check=False,
		)
	except (OSError, subprocess.SubprocessError) as exc:
		return ProcessOutput(returncode=-1, stdout="", stderr=f"{type(exc).__name__}: {exc}")
	return ProcessOutput(
		returncode=result.returncode,
		stdout=result.stdout,
		stderr=result.stderr,
	)


def find_artifact_root(workspace: Path) -> Path | None:
	"""Find the wrapper root in the layouts used by direct and managed runs."""
	for candidate in (workspace, workspace / "artifact"):
		if (
			(candidate / ".gitmodules").is_file()
			and (candidate / "paralegal").is_dir()
			and (candidate / "paralegal-bench").is_dir()
			and (candidate / "codeql-experimentation").is_dir()
		):
			return candidate
	return None


def load_json_object(path: Path) -> dict[str, Any]:
	data = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(data, dict):
		raise ValueError(f"expected a JSON object in {path}")
	return data


def load_toml(path: Path) -> dict[str, Any]:
	with path.open("rb") as handle:
		data = tomllib.load(handle)
	if not isinstance(data, dict):
		raise ValueError(f"expected a TOML table in {path}")
	return data


def sha256_file(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def load_expected_manifest(path: Path) -> tuple[ExpectedOutputEntry, ...]:
	data = load_json_object(path)
	if data.get("schema_version") != 1:
		raise ValueError("CodeQL expected-output manifest has an unsupported schema")
	if data.get("codeql_version") != CODEQL_VERSION:
		raise ValueError(f"CodeQL expected-output manifest must target {CODEQL_VERSION}")
	if data.get("source_commit") != CODEQL_COMMIT:
		raise ValueError(f"CodeQL expected-output manifest must target commit {CODEQL_COMMIT}")

	raw_entries = data.get("entries")
	if not isinstance(raw_entries, list) or len(raw_entries) != 10:
		raise ValueError("CodeQL expected-output manifest must contain 10 entries")

	entries: list[ExpectedOutputEntry] = []
	expected_paths: set[str] = set()
	result_paths: set[str] = set()
	for index, raw in enumerate(raw_entries):
		if not isinstance(raw, dict):
			raise ValueError(f"CodeQL manifest entry {index} is not an object")
		expected_path = _safe_relative_path(raw.get("expected_path"), "expected_path")
		result_path = _safe_relative_path(raw.get("result_path"), "result_path")
		sha256 = raw.get("sha256")
		byte_count = raw.get("bytes")
		row_count = raw.get("rows")
		if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
			raise ValueError(f"CodeQL manifest entry {index} has an invalid SHA-256")
		if not isinstance(byte_count, int) or byte_count <= 0:
			raise ValueError(f"CodeQL manifest entry {index} has an invalid byte count")
		if not isinstance(row_count, int) or row_count < 0:
			raise ValueError(f"CodeQL manifest entry {index} has an invalid row count")
		if expected_path in expected_paths or result_path in result_paths:
			raise ValueError(f"CodeQL manifest entry {index} duplicates a path")
		expected_paths.add(expected_path)
		result_paths.add(result_path)
		entries.append(
			ExpectedOutputEntry(
				expected_path=expected_path,
				result_path=result_path,
				sha256=sha256,
				bytes=byte_count,
				rows=row_count,
			)
		)
	return tuple(entries)


def validate_expected_files(
	codeql_root: Path,
	entries: tuple[ExpectedOutputEntry, ...],
) -> tuple[str, ...]:
	"""Return provenance errors for the checked-out CodeQL expected tables."""
	errors: list[str] = []
	for entry in entries:
		path = codeql_root / entry.expected_path
		if not path.is_file():
			errors.append(f"missing {entry.expected_path}")
			continue
		try:
			raw = path.read_bytes()
			table = parse_codeql_table(raw.decode("utf-8"))
		except (OSError, UnicodeError, ValueError) as exc:
			errors.append(f"{entry.expected_path}: {exc}")
			continue
		if len(raw) != entry.bytes:
			errors.append(
				f"{entry.expected_path}: observed {len(raw)} bytes, expected {entry.bytes}"
			)
		if hashlib.sha256(raw).hexdigest() != entry.sha256:
			errors.append(f"{entry.expected_path}: SHA-256 mismatch")
		if len(table.rows) != entry.rows:
			errors.append(
				f"{entry.expected_path}: observed {len(table.rows)} rows, expected {entry.rows}"
			)
	return tuple(errors)


def parse_codeql_table(text: str) -> CodeQLTable:
	"""Parse a CodeQL ASCII table into normalized, order-insensitive data."""
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	if not lines:
		raise ValueError("CodeQL table is empty")
	if len(lines) < 2:
		raise ValueError("CodeQL table is incomplete")
	if not lines[0].startswith("|") or not lines[0].endswith("|"):
		raise ValueError("CodeQL table header is malformed")
	if not _is_table_separator(lines[1]):
		raise ValueError("CodeQL table separator is missing or malformed")

	header = _parse_table_row(lines[0])
	if not header or any(not cell for cell in header):
		raise ValueError("CodeQL table contains an empty header")

	rows: list[tuple[str, ...]] = []
	for line_number, line in enumerate(lines[2:], start=3):
		if _is_table_separator(line):
			raise ValueError(f"unexpected CodeQL separator on line {line_number}")
		row = _parse_table_row(line)
		if len(row) != len(header):
			raise ValueError(
				f"CodeQL row {line_number} has {len(row)} columns; expected {len(header)}"
			)
		rows.append(row)

	return CodeQLTable(header=header, rows=tuple(sorted(rows)))


def parse_smoke_results(text: str) -> SmokeResults:
	"""Parse and validate the bounded atomic-data benchmark CSV."""
	if not text.strip():
		raise ValueError("smoke results CSV is empty")
	reader = csv.DictReader(io.StringIO(text))
	fieldnames = set(reader.fieldnames or ())
	missing = sorted(_REQUIRED_SMOKE_COLUMNS - fieldnames)
	if missing:
		raise ValueError("smoke results CSV is missing columns: " + ", ".join(missing))

	rows = tuple(dict(row) for row in reader)
	if not rows:
		raise ValueError("smoke results CSV has no data rows")
	if len(rows) != 2:
		raise ValueError(f"smoke results CSV must contain exactly 2 rows, found {len(rows)}")
	if any(None in row for row in rows):
		raise ValueError("smoke results CSV contains a malformed row")

	run_ids: set[int] = set()
	expectations: set[str] = set()
	for index, row in enumerate(rows, start=2):
		if row["experiment"].strip() != "smoke":
			raise ValueError(f"row {index} is not from experiment=smoke")
		if row["application"].strip() != "atomic-data":
			raise ValueError(f"row {index} is not from application=atomic-data")

		expectation = row["expectation"].strip().lower()
		result = row["result"].strip().lower()
		if expectation not in {"pass", "fail"}:
			raise ValueError(f"row {index} has invalid expectation {expectation!r}")
		if result != expectation:
			raise ValueError(
				f"row {index} result {result!r} does not match expectation {expectation!r}"
			)
		expectations.add(expectation)

		if row["pdg_timed_out"].strip().lower() not in {"false", "0", "no"}:
			raise ValueError(f"row {index} reports a PDG timeout")

		try:
			run_id = int(row["id"])
		except (TypeError, ValueError) as exc:
			raise ValueError(f"row {index} has an invalid id") from exc
		if run_id in run_ids:
			raise ValueError(f"row {index} duplicates run id {run_id}")
		run_ids.add(run_id)

		for column in _POSITIVE_SMOKE_COLUMNS:
			value = _positive_float(row[column], row=index, column=column)
			if not math.isfinite(value):
				raise ValueError(f"row {index} column {column} is not finite")

	if expectations != {"pass", "fail"}:
		raise ValueError("smoke results must include both expected pass and expected fail runs")

	return SmokeResults(rows=rows, run_ids=frozenset(run_ids))


def validate_controller_results(text: str, *, expected_run_ids: frozenset[int]) -> int:
	"""Validate controller metrics and return the number of controller rows."""
	if not text.strip():
		raise ValueError("controllers CSV is empty")
	reader = csv.DictReader(io.StringIO(text))
	fieldnames = set(reader.fieldnames or ())
	missing = sorted(_REQUIRED_CONTROLLER_COLUMNS - fieldnames)
	if missing:
		raise ValueError("controllers CSV is missing columns: " + ", ".join(missing))

	rows = tuple(dict(row) for row in reader)
	if not rows:
		raise ValueError("controllers CSV has no data rows")
	if any(None in row for row in rows):
		raise ValueError("controllers CSV contains a malformed row")

	seen_run_ids: set[int] = set()
	for index, row in enumerate(rows, start=2):
		try:
			run_id = int(row["run_id"])
		except (TypeError, ValueError) as exc:
			raise ValueError(f"controller row {index} has an invalid run_id") from exc
		if run_id not in expected_run_ids:
			raise ValueError(f"controller row {index} references unknown run id {run_id}")
		seen_run_ids.add(run_id)
		if not row["name"].strip():
			raise ValueError(f"controller row {index} has no controller name")
		for column in _POSITIVE_CONTROLLER_COLUMNS:
			_positive_float(row[column], row=index, column=column)

	if seen_run_ids != set(expected_run_ids):
		missing_ids = sorted(set(expected_run_ids) - seen_run_ids)
		raise ValueError(f"controllers CSV has no rows for run ids: {missing_ids}")
	return len(rows)


def latest_result_directory(parent: Path, *, suffix: str = "") -> Path:
	if not parent.is_dir():
		raise ValueError(f"result directory is missing: {parent}")
	candidates = sorted(
		path for path in parent.iterdir() if path.is_dir() and path.name.endswith(suffix)
	)
	if not candidates:
		label = f"*{suffix}" if suffix else "timestamped"
		raise ValueError(f"no {label} result directories found under {parent}")
	return candidates[-1]


def _safe_relative_path(value: object, field_name: str) -> str:
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"CodeQL manifest {field_name} must be a relative path")
	path = Path(value)
	if path.is_absolute() or ".." in path.parts:
		raise ValueError(f"CodeQL manifest {field_name} leaves its expected root")
	return path.as_posix()


def _normalize_cell(cell: str) -> str:
	normalized = " ".join(cell.split())
	return _CODEQL_ROOT_RE.sub(
		"file:///<ROOT>/codeql-experimentation/",
		normalized,
	)


def _parse_table_row(line: str) -> tuple[str, ...]:
	if not line.startswith("|") or not line.endswith("|"):
		raise ValueError("CodeQL data row is malformed")
	return tuple(_normalize_cell(cell) for cell in line[1:-1].split("|"))


def _is_table_separator(line: str) -> bool:
	if not line.startswith("+") or not line.endswith("+"):
		return False
	segments = line[1:-1].split("+")
	return bool(segments) and all(segment and set(segment) == {"-"} for segment in segments)


def _positive_float(value: object, *, row: int, column: str) -> float:
	try:
		number = float(str(value).strip())
	except (TypeError, ValueError) as exc:
		raise ValueError(f"row {row} column {column} is not numeric") from exc
	if not math.isfinite(number):
		raise ValueError(f"row {row} column {column} is not finite")
	if number <= 0:
		raise ValueError(f"row {row} column {column} must be positive")
	return number
