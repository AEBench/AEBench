from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	check_read_file_text,
	glob,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_UPROBE_OPERATIONS = (
	"__bench_uprobe",
	"__bench_uretprobe",
	"__bench_read",
	"__bench_write",
	"__bench_hash_map_update",
	"__bench_hash_map_lookup",
	"__bench_hash_map_delete",
)
_UPROBE_ENVS = ("kernel_uprobe", "userspace_uprobe")
_SYSCALL_ENVS = ("native", "kernel_tracepoint", "userspace_syscall")
_LOG_FAILURE_RE = re.compile(
	r"\b(?:error|traceback)\b|no valid results",
	re.IGNORECASE,
)


def _join(base: OraclePath, name: str) -> OraclePath:
	if isinstance(base, RuntimePath):
		return RuntimePath.from_parts(base.value, name)
	return Path(base, name)


def _load_json(text: str, label: str) -> dict[str, object]:
	try:
		value = json.loads(text, parse_constant=lambda token: float(token))
	except json.JSONDecodeError as exc:
		raise ValueError(f"{label}: invalid JSON: {exc}") from exc
	if not isinstance(value, dict):
		raise ValueError(f"{label}: expected a JSON object")
	return value


def _number(value: object, label: str, *, allow_zero: bool = False) -> float:
	if not isinstance(value, (int, float)) or isinstance(value, bool):
		raise ValueError(f"{label}: expected a number")
	number = float(value)
	if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
		raise ValueError(f"{label}: invalid value {value!r}")
	return number


def _samples(value: object, label: str, *, expected_count: int) -> list[float]:
	if not isinstance(value, list):
		raise ValueError(f"{label}: expected raw sample list")
	values = [_number(item, f"{label}[{index}]") for index, item in enumerate(value)]
	if len(values) != expected_count or len(values) < 3:
		raise ValueError(
			f"{label}: found {len(values)} samples, expected {expected_count} (at least 3)"
		)
	return values


def _close(observed: float, expected: float) -> bool:
	return math.isclose(observed, expected, rel_tol=1e-8, abs_tol=1e-8)


def _uprobe_metrics(value: object, label: str, *, sample_count: int) -> float:
	if not isinstance(value, dict):
		raise ValueError(f"{label}: expected a metrics object")
	values = _samples(value.get("raw_values"), f"{label}.raw_values", expected_count=sample_count)
	average = sum(values) / len(values)
	variance = sum(item**2 for item in values) / len(values) - average**2
	expected = {
		"avg": average,
		"min": min(values),
		"max": max(values),
		"std_dev": math.sqrt(variance) if variance > 0 else 0.0,
	}
	for metric, calculated in expected.items():
		observed = _number(value.get(metric), f"{label}.{metric}", allow_zero=metric == "std_dev")
		if not _close(observed, calculated):
			raise ValueError(
				f"{label}.{metric}: {observed:.6g} does not match raw samples ({calculated:.6g})"
			)
	return expected["avg"]


def _syscall_metrics(value: object, label: str, *, sample_count: int) -> float:
	if not isinstance(value, dict) or value.get("status") == "no_data":
		raise ValueError(f"{label}: expected completed metrics")
	values = _samples(value.get("raw_values"), f"{label}.raw_values", expected_count=sample_count)
	expected = {
		"avg": statistics.fmean(values),
		"median": statistics.median(values),
		"min": min(values),
		"max": max(values),
		"stdev": statistics.stdev(values),
	}
	for metric, calculated in expected.items():
		observed = _number(value.get(metric), f"{label}.{metric}", allow_zero=metric == "stdev")
		if not _close(observed, calculated):
			raise ValueError(
				f"{label}.{metric}: {observed:.6g} does not match raw samples ({calculated:.6g})"
			)
	return expected["avg"]


def _reference_relation(value: object, label: str) -> str:
	if not isinstance(value, dict):
		raise ValueError(f"{label}: invalid reference entry")
	ebpf = _number(value.get("ebpf_ns"), f"{label}.ebpf_ns")
	bpftime = _number(value.get("bpftime_ns"), f"{label}.bpftime_ns")
	if bpftime == ebpf:
		raise ValueError(f"{label}: reference values have no performance direction")
	return "bpftime_faster" if bpftime < ebpf else "ebpf_faster"


def _relation_holds(kernel: float, userspace: float, relation: str) -> bool:
	return userspace < kernel if relation == "bpftime_faster" else kernel < userspace


@dataclass(frozen=True, slots=True, kw_only=True)
class BpftimeTable3Check(BaseCheck):
	uprobe_results: OraclePath
	uprobe_log: OraclePath
	syscall_dir: OraclePath
	syscall_pattern: str
	reference_path: Path

	def _load_uprobe(self, executor: RuntimeCheckExecutor) -> dict[str, dict[str, float]]:
		root = _load_json(
			check_read_file_text(self.uprobe_results, executor=executor), "uprobe results"
		)
		info = root.get("benchmark_info")
		if not isinstance(info, dict):
			raise ValueError("uprobe results: missing benchmark_info")
		runs = info.get("num_runs")
		if not isinstance(runs, int) or isinstance(runs, bool) or runs < 3:
			raise ValueError("uprobe results: expected at least three runs")

		observed: dict[str, dict[str, float]] = {}
		for environment in _UPROBE_ENVS:
			entries = root.get(environment)
			if not isinstance(entries, dict):
				raise ValueError(f"uprobe results: missing {environment}")
			missing = sorted(set(_UPROBE_OPERATIONS) - entries.keys())
			if missing:
				raise ValueError(f"uprobe results: {environment} missing {missing}")
			observed[environment] = {
				operation: _uprobe_metrics(
					entries[operation],
					f"{environment}.{operation}",
					sample_count=runs,
				)
				for operation in _UPROBE_OPERATIONS
			}
		return observed

	def _load_syscall(self, executor: RuntimeCheckExecutor) -> dict[str, float]:
		matches = glob(self.syscall_dir, self.syscall_pattern, executor=executor)
		if not matches:
			raise ValueError("syscall results: no timestamped JSON output found")
		latest = max(matches, key=lambda path: path.name)
		root = _load_json(
			check_read_file_text(_join(self.syscall_dir, latest.name), executor=executor),
			latest.name,
		)
		runs = root.get("runs")
		if not isinstance(runs, int) or isinstance(runs, bool) or runs < 3:
			raise ValueError("syscall results: expected at least three runs")
		entries = root.get("results")
		if not isinstance(entries, dict) or set(entries) != set(_SYSCALL_ENVS):
			raise ValueError("syscall results: expected native, kernel, and userspace entries")
		return {
			environment: _syscall_metrics(
				entries[environment],
				f"syscall.{environment}",
				sample_count=runs,
			)
			for environment in _SYSCALL_ENVS
		}

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			reference = _load_json(self.reference_path.read_text(encoding="utf-8"), "reference")
			operation_refs = reference.get("operations")
			if not isinstance(operation_refs, dict) or set(operation_refs) != set(
				_UPROBE_OPERATIONS
			):
				raise ValueError("reference: unexpected Table 3 operation set")
			uprobe = self._load_uprobe(executor)
			syscall = self._load_syscall(executor)
			log = check_read_file_text(self.uprobe_log, executor=executor)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"invalid bpftime benchmark evidence: {exc}")

		errors: list[str] = []
		for label, pattern in (
			("kernel start", r"^\[[^\n]+\] Starting kernel uprobe test with \d+ runs$"),
			("kernel completion", r"^\[[^\n]+\] Kernel uprobe test completed with results:"),
			("userspace start", r"^\[[^\n]+\] Starting userspace uprobe test with \d+ runs$"),
			(
				"userspace completion",
				r"^\[[^\n]+\] Userspace uprobe test completed with results:",
			),
		):
			if len(re.findall(pattern, log, re.MULTILINE)) != 1:
				errors.append(f"uprobe log: expected one {label} marker")
		if _LOG_FAILURE_RE.search(log):
			errors.append("uprobe log reports an error, traceback, or missing result")

		for operation in _UPROBE_OPERATIONS:
			try:
				relation = _reference_relation(operation_refs[operation], f"reference.{operation}")
			except ValueError as exc:
				errors.append(str(exc))
				continue
			kernel = uprobe["kernel_uprobe"][operation]
			userspace = uprobe["userspace_uprobe"][operation]
			if not _relation_holds(kernel, userspace, relation):
				errors.append(
					f"{operation}: observed kernel={kernel:.3f} ns, bpftime={userspace:.3f} ns "
					f"does not preserve {relation}"
				)

		try:
			syscall_relation = _reference_relation(
				reference.get("syscall_tracepoint"), "reference.syscall_tracepoint"
			)
		except ValueError as exc:
			errors.append(str(exc))
		else:
			if not _relation_holds(
				syscall["kernel_tracepoint"],
				syscall["userspace_syscall"],
				syscall_relation,
			):
				errors.append("syscall tracepoint does not preserve the paper-reported tradeoff")

		if errors:
			return CheckResult.failure("Table 3 mismatch: " + "; ".join(errors))
		return CheckResult.success(
			"all eight Table 3 operations have complete samples, consistent statistics, "
			"and the paper-reported performance direction"
		)
