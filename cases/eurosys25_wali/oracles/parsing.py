from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import BENCHMARK_APPS

_CSV_HEADER = ("wall", "utime", "stime", "maxrss", "benchmark", "mode")
_NUMERIC_FIELDS = _CSV_HEADER[:4]
_BEGIN_RE = re.compile(r"^===== BEGIN ([A-Za-z0-9_-]+) =====$")
_END_RE = re.compile(r"^===== END ([A-Za-z0-9_-]+) status=([0-9]+) =====$")
_SYSCALL_RE = re.compile(r"WALI:\s+SC\s+\|\s+([A-Za-z0-9_]+)")
_CTEST_RE = re.compile(r"\bTest\s+#(\d+):.*\bPassed\b")
_BENCHMARK_MODES = ("native", "docker", "wali", "qemu", "docker-inner")

_PORTABILITY_SIGNATURES = {
	"bash": "GNU bash",
	"sqlite3": "3.45.0",
	"vim": "Vi IMproved 9.0",
	"memcached": "memcached",
}


def _runtime_join(base: OraclePath, *parts: str) -> OraclePath:
	if isinstance(base, RuntimePath):
		return RuntimePath.from_parts(base.value, *parts)
	return Path(base).joinpath(*parts)


def _read_runtime_text(path: OraclePath, executor: RuntimeCheckExecutor) -> str:
	try:
		return check_read_file_text(path, encoding="utf-8", executor=executor)
	except (OSError, RuntimeError, ValueError) as exc:
		raise ValueError(f"could not read {path}: {exc}") from exc


def _read_runtime_bytes(path: OraclePath, executor: RuntimeCheckExecutor) -> bytes:
	resolved = executor.resolve_path(path)
	if not isinstance(resolved, Path):
		raise ValueError(f"binary file inspection requires a local runtime: {path}")
	try:
		return resolved.read_bytes()
	except OSError as exc:
		raise ValueError(f"could not read {path}: {exc}") from exc


def _load_reference(path: Path) -> dict[str, object]:
	try:
		value = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as exc:
		raise ValueError(f"could not read reference {path}: {exc}") from exc
	if not isinstance(value, dict):
		raise ValueError("reference must contain a JSON object")
	return value


def _parse_status_sections(text: str) -> dict[str, str]:
	sections: dict[str, str] = {}
	current: str | None = None
	body: list[str] = []

	for line in text.splitlines():
		if match := _BEGIN_RE.fullmatch(line.strip()):
			name = match.group(1)
			if current is not None:
				raise ValueError(f"section {name!r} begins before {current!r} ends")
			if name in sections:
				raise ValueError(f"duplicate section {name!r}")
			current, body = name, []
			continue

		if match := _END_RE.fullmatch(line.strip()):
			name, status = match.groups()
			if current != name:
				raise ValueError(f"section end {name!r} does not match {current!r}")
			if status != "0":
				raise ValueError(f"section {name!r} ended with status {status}")
			sections[name] = "\n".join(body)
			current, body = None, []
			continue

		if current is not None:
			body.append(line)

	if current is not None:
		raise ValueError(f"section {current!r} has no end marker")
	return sections


def _linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
	pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if x < 4 and y < 4]
	if len(pairs) < 2:
		raise ValueError("linear fit requires at least two paired values below 4 seconds")
	x_mean = sum(x for x, _y in pairs) / len(pairs)
	y_mean = sum(y for _x, y in pairs) / len(pairs)
	denominator = sum((x - x_mean) ** 2 for x, _y in pairs)
	if denominator <= 0:
		raise ValueError("linear fit requires varying native runtimes")
	slope = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
	slope /= denominator
	return y_mean - slope * x_mean, slope


@dataclass(frozen=True, slots=True, kw_only=True)
class PortabilityLogCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sections = _parse_status_sections(_read_runtime_text(self.path, executor))
		except ValueError as exc:
			return CheckResult.failure(f"portability log: {exc}")

		missing = sorted(_PORTABILITY_SIGNATURES.keys() - sections.keys())
		if missing:
			return CheckResult.failure(
				"portability log missing applications: " + ", ".join(missing)
			)

		failures: list[str] = []
		for app, signature in _PORTABILITY_SIGNATURES.items():
			body = sections[app]
			if signature not in body:
				failures.append(f"{app}: missing output signature {signature!r}")
			syscalls = frozenset(_SYSCALL_RE.findall(body))
			if len(syscalls) < 5:
				failures.append(f"{app}: only {len(syscalls)} distinct WALI syscalls traced")

		if failures:
			return CheckResult.failure("; ".join(failures))
		return CheckResult.success("four ported applications completed with dynamic WALI traces")


@dataclass(frozen=True, slots=True, kw_only=True)
class WasiLayeringLogCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			text = _read_runtime_text(self.path, executor)
		except ValueError as exc:
			return CheckResult.failure(f"libuvwasi CTest log: {exc}")

		passed = {int(test_number) for test_number in _CTEST_RE.findall(text)}
		if passed != set(range(1, 23)):
			missing = sorted(set(range(1, 23)) - passed)
			return CheckResult.failure(
				f"libuvwasi CTest log has {len(passed)}/22 passed tests; missing {missing}"
			)
		if not re.search(r"100% tests passed,\s*0 tests failed out of 22", text):
			return CheckResult.failure("libuvwasi CTest log lacks the 22-test zero-failure summary")
		if re.search(r"\*\*\*(?:Failed|Timeout)\b", text):
			return CheckResult.failure("libuvwasi CTest log reports a failure or timeout")
		return CheckResult.success("all 22 libuvwasi tests passed")


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkLogCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			sections = _parse_status_sections(_read_runtime_text(self.path, executor))
		except ValueError as exc:
			return CheckResult.failure(f"benchmark log: {exc}")
		missing = sorted(set(_BENCHMARK_MODES) - sections.keys())
		if missing:
			return CheckResult.failure("benchmark log missing modes: " + ", ".join(missing))
		return CheckResult.success("all five benchmark modes completed with status 0")


def _parse_summary_csv(text: str, app: str) -> dict[tuple[str, str], dict[str, float]]:
	try:
		rows = list(csv.reader(text.splitlines()))
	except csv.Error as exc:
		raise ValueError(f"could not parse {app}.csv: {exc}") from exc
	if not rows or tuple(rows[0]) != _CSV_HEADER:
		raise ValueError(f"{app}.csv header must be {','.join(_CSV_HEADER)}")

	parsed: dict[tuple[str, str], dict[str, float]] = {}
	for line_number, row in enumerate(rows[1:], start=2):
		if len(row) != len(_CSV_HEADER):
			raise ValueError(
				f"{app}.csv line {line_number} has {len(row)} columns; expected {len(_CSV_HEADER)}"
			)
		values = dict(zip(_CSV_HEADER, row, strict=True))
		key, mode = values["benchmark"].strip(), values["mode"].strip()
		if not key or not mode:
			raise ValueError(f"{app}.csv line {line_number} has an empty benchmark or mode")
		row_key = (key, mode)
		if row_key in parsed:
			raise ValueError(f"{app}.csv has duplicate row {row_key}")

		numbers: dict[str, float] = {}
		for field_name in _NUMERIC_FIELDS:
			try:
				value = float(values[field_name])
			except ValueError as exc:
				raise ValueError(f"{app}.csv line {line_number} has invalid {field_name}") from exc
			if not math.isfinite(value):
				raise ValueError(f"{app}.csv line {line_number} has non-finite {field_name}")
			if value < 0 or (field_name in {"wall", "maxrss"} and value == 0):
				raise ValueError(f"{app}.csv line {line_number} has invalid {field_name}={value}")
			numbers[field_name] = value
		parsed[row_key] = numbers
	return parsed


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkResultsCheck(BaseCheck):
	results_dir: OraclePath
	reference_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			reference = _load_reference(self.reference_path)
			benchmark_refs = reference["benchmarks"]
			if not isinstance(benchmark_refs, dict):
				raise ValueError("reference benchmarks must be an object")
		except (KeyError, ValueError) as exc:
			return CheckResult.failure(f"benchmark reference: {exc}")

		all_rows: dict[str, dict[tuple[str, str], dict[str, float]]] = {}
		for app in BENCHMARK_APPS:
			try:
				app_ref = benchmark_refs[app]
				if not isinstance(app_ref, dict):
					raise ValueError(f"reference {app} entry must be an object")
				expected_keys = {str(key) for key in app_ref["keys"]}
				path = _runtime_join(self.results_dir, f"{app}.csv")
				rows = _parse_summary_csv(_read_runtime_text(path, executor), app)
			except (KeyError, TypeError, ValueError) as exc:
				return CheckResult.failure(str(exc))

			expected_rows = {(key, mode) for key in expected_keys for mode in _BENCHMARK_MODES}
			missing = sorted(expected_rows - rows.keys())
			extra = sorted(rows.keys() - expected_rows)
			if missing or extra:
				return CheckResult.failure(
					f"{app}.csv row mismatch: missing {missing[:6]}, unexpected {extra[:6]}"
				)
			for key, mode in expected_rows:
				raw_path = _runtime_join(self.results_dir, app, f"{key}.{mode}")
				try:
					raw = next(csv.reader([_read_runtime_text(raw_path, executor).strip()]))
					raw_values = [float(value) for value in raw]
				except (csv.Error, StopIteration, ValueError) as exc:
					return CheckResult.failure(f"malformed raw result {app}/{key}.{mode}: {exc}")
				if (
					len(raw_values) != 4
					or not all(math.isfinite(value) for value in raw_values)
					or raw_values[0] <= 0
					or raw_values[1] < 0
					or raw_values[2] < 0
					or raw_values[3] <= 0
				):
					return CheckResult.failure(
						f"raw result {app}/{key}.{mode} must contain valid wall, CPU, and RSS values"
					)
				if raw_values != [rows[(key, mode)][field] for field in _NUMERIC_FIELDS]:
					return CheckResult.failure(
						f"raw result {app}/{key}.{mode} does not match its summary row"
					)
			all_rows[app] = rows

		claims: list[str] = []
		for app, rows in all_rows.items():
			keys = sorted({key for key, _mode in rows})
			native_wall = [rows[(key, "native")]["wall"] for key in keys]
			wali_wall = [rows[(key, "wali")]["wall"] for key in keys]
			docker_wall = [rows[(key, "docker")]["wall"] for key in keys]
			qemu_wall = [rows[(key, "qemu")]["wall"] for key in keys]

			try:
				wali_intercept, wali_slope = _linear_fit(native_wall, wali_wall)
				docker_intercept, _docker_slope = _linear_fit(native_wall, docker_wall)
				_qemu_intercept, qemu_slope = _linear_fit(native_wall, qemu_wall)
			except ValueError as exc:
				return CheckResult.failure(f"{app}: {exc}")

			memory_ratios = [
				rows[(key, "wali")]["maxrss"]
				/ (rows[(key, "docker")]["maxrss"] + rows[(key, "docker-inner")]["maxrss"])
				for key in keys
			]
			if wali_intercept >= docker_intercept:
				claims.append(
					f"{app}: WALI startup intercept {wali_intercept:.4g} "
					f">= Docker {docker_intercept:.4g}"
				)
			if wali_slope >= qemu_slope:
				claims.append(
					f"{app}: WALI execution slope {wali_slope:.4g} >= QEMU {qemu_slope:.4g}"
				)
			if median(memory_ratios) >= 1:
				claims.append(f"{app}: median WALI/Docker memory ratio is not below 1")

		if claims:
			return CheckResult.failure("; ".join(claims))
		return CheckResult.success(
			"complete fresh results preserve WALI's startup, memory, and QEMU execution relations"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfOutputsCheck(BaseCheck):
	paths: Sequence[OraclePath]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		invalid: list[str] = []
		for path in self.paths:
			name = Path(str(path)).name
			try:
				content = _read_runtime_bytes(path, executor)
			except ValueError:
				invalid.append(name)
				continue
			if len(content) < 1_000 or not content.startswith(b"%PDF-"):
				invalid.append(name)
				continue
		if invalid:
			return CheckResult.failure("missing or malformed generated PDFs: " + ", ".join(invalid))
		return CheckResult.success(f"all {len(self.paths)} benchmark PDFs are valid")
