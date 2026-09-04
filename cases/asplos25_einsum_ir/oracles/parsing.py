from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import WORKLOAD_CONFIGS

_MIN_SAMPLES = 3
_MAX_RELATIVE_OUTPUT_ERROR = 1e-4
_MIN_ATEN_WINS = 6
_MIN_GEOMEAN_SPEEDUP = 1.5

_FLOAT_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_COMPARISON_PATTERN = re.compile(
	r"maximum absolute entry in ATen solution:\s+"
	+ rf"(?P<aten_max>{_FLOAT_PATTERN})\s+"
	+ r"maximum absolute entry in einsum_ir solution:\s+"
	+ rf"(?P<einsum_max>{_FLOAT_PATTERN})\s+"
	+ r"maximum element-wise difference:\s+"
	+ rf"(?P<max_difference>{_FLOAT_PATTERN})"
)


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
	code: str
	time_compile: float
	time_eval: float
	gflops_eval: float
	gflops_total: float


def _parse_reference(path: Path) -> dict[str, tuple[int, bool]]:
	payload = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(payload, dict):
		raise ValueError("workload reference must be an object")

	expected_names = {name for name, _ in WORKLOAD_CONFIGS}
	if set(payload) != expected_names:
		raise ValueError("workload reference has an unexpected workload set")

	reference: dict[str, tuple[int, bool]] = {}
	for workload, entry in payload.items():
		if not isinstance(entry, dict):
			raise ValueError(f"{workload}: reference entry must be an object")
		flops = entry.get("flops")
		aten_comparable = entry.get("aten_comparable")
		if not isinstance(flops, int) or isinstance(flops, bool) or flops <= 0:
			raise ValueError(f"{workload}: invalid operation count")
		if not isinstance(aten_comparable, bool):
			raise ValueError(f"{workload}: invalid ATen comparison flag")
		reference[workload] = (flops, aten_comparable)
	return reference


def _parse_rows(text: str, *, workload: str, expected_flops: int) -> list[BenchmarkRow]:
	rows: list[BenchmarkRow] = []
	for line_number, line in enumerate(text.splitlines(), start=1):
		if not line.startswith("CSV_DATA: "):
			continue
		try:
			fields = next(csv.reader(io.StringIO(line.removeprefix("CSV_DATA: ")), strict=True))
		except csv.Error as exc:
			raise ValueError(f"{workload}:{line_number}: malformed CSV_DATA row: {exc}") from exc
		if len(fields) != 9:
			raise ValueError(
				f"{workload}:{line_number}: expected 9 CSV_DATA fields, got {len(fields)}"
			)
		if not all(fields[index].strip() for index in (0, 1, 2, 3)):
			raise ValueError(f"{workload}:{line_number}: empty benchmark identity field")
		try:
			flops = int(fields[4])
			values = tuple(float(value) for value in fields[5:9])
		except ValueError as exc:
			raise ValueError(f"{workload}:{line_number}: invalid numeric field") from exc
		if flops != expected_flops:
			raise ValueError(
				f"{workload}:{line_number}: got {flops} operations, expected {expected_flops}"
			)
		if not all(math.isfinite(value) and value >= 0 for value in values):
			raise ValueError(f"{workload}:{line_number}: metrics must be finite and nonnegative")
		rows.append(BenchmarkRow(fields[0], *values))
	return rows


def _validate_rows(
	rows: Sequence[BenchmarkRow],
	*,
	workload: str,
	aten_comparable: bool,
) -> tuple[float, float | None]:
	counts = Counter(row.code for row in rows)
	expected_codes = {"einsum_ir", "at::matmul"}
	if aten_comparable:
		expected_codes.add("at::einsum")
	if set(counts) != expected_codes:
		raise ValueError(f"{workload}: unexpected benchmark codes {sorted(counts)}")
	if len(set(counts.values())) != 1:
		raise ValueError(f"{workload}: benchmark code sample counts differ: {dict(counts)}")
	sample_count = counts["einsum_ir"]
	if sample_count < _MIN_SAMPLES:
		raise ValueError(
			f"{workload}: only {sample_count} samples, expected at least {_MIN_SAMPLES}"
		)

	for row in rows:
		if row.code == "einsum_ir":
			if not all(
				value > 0
				for value in (
					row.time_compile,
					row.time_eval,
					row.gflops_eval,
					row.gflops_total,
				)
			):
				raise ValueError(f"{workload}: Einsum IR metrics must be positive")
		elif row.code == "at::einsum":
			if (row.time_compile, row.time_eval, row.gflops_eval) != (0, 0, 0):
				raise ValueError(f"{workload}: ATen unused metrics must be zero")
			if row.gflops_total <= 0:
				raise ValueError(f"{workload}: ATen total performance must be positive")
		else:
			if row.time_compile != 0 or row.gflops_total != 0:
				raise ValueError(f"{workload}: matmul unused metrics must be zero")
			if row.time_eval <= 0 or row.gflops_eval <= 0:
				raise ValueError(f"{workload}: matmul evaluation metrics must be positive")

	einsum_gflops = statistics.median(row.gflops_eval for row in rows if row.code == "einsum_ir")
	if not aten_comparable:
		return einsum_gflops, None
	aten_gflops = statistics.median(row.gflops_total for row in rows if row.code == "at::einsum")
	return einsum_gflops, aten_gflops


def _validate_comparisons(text: str, *, workload: str, expected_count: int) -> None:
	matches = list(_COMPARISON_PATTERN.finditer(text))
	if len(matches) != expected_count:
		raise ValueError(
			f"{workload}: found {len(matches)} complete numerical comparisons, "
			f"expected {expected_count}"
		)

	for match in matches:
		aten_max = float(match.group("aten_max"))
		einsum_max = float(match.group("einsum_max"))
		difference = float(match.group("max_difference"))
		if not all(
			math.isfinite(value) and value >= 0 for value in (aten_max, einsum_max, difference)
		):
			raise ValueError(f"{workload}: comparison values must be finite and nonnegative")
		scale = max(aten_max, einsum_max)
		if scale <= 0:
			raise ValueError(f"{workload}: comparison output magnitude must be positive")
		if difference / scale > _MAX_RELATIVE_OUTPUT_ERROR:
			raise ValueError(
				f"{workload}: relative maximum difference {difference / scale:.3g} exceeds "
				f"{_MAX_RELATIVE_OUTPUT_ERROR:.3g}"
			)


@dataclass(frozen=True, slots=True, kw_only=True)
class EinsumEvaluationCheck(BaseCheck):
	logs: Mapping[str, OraclePath]
	reference_path: Path

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			reference = _parse_reference(self.reference_path)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return CheckResult.failure(f"could not read workload reference: {exc}")

		errors: list[str] = []
		speedups: list[float] = []
		wins = 0
		for workload, _ in WORKLOAD_CONFIGS:
			expected_flops, aten_comparable = reference[workload]
			try:
				text = check_read_file_text(self.logs[workload], executor=executor)
				rows = _parse_rows(text, workload=workload, expected_flops=expected_flops)
				einsum_gflops, aten_gflops = _validate_rows(
					rows,
					workload=workload,
					aten_comparable=aten_comparable,
				)
				sample_count = sum(row.code == "einsum_ir" for row in rows)
				if text.count("dtype: FP32") != sample_count:
					raise ValueError(f"{workload}: FP32 marker count does not match samples")
				if aten_gflops is not None:
					_validate_comparisons(
						text,
						workload=workload,
						expected_count=sample_count,
					)
					speedup = einsum_gflops / aten_gflops
					speedups.append(speedup)
					wins += speedup > 1.0
			except (OSError, RuntimeError, ValueError) as exc:
				errors.append(str(exc))

		if errors:
			return CheckResult.failure("invalid Einsum IR evaluation: " + "; ".join(errors))
		if wins < _MIN_ATEN_WINS:
			return CheckResult.failure(
				f"TPP beat ATen on {wins}/{len(speedups)} workloads; expected at least {_MIN_ATEN_WINS}"
			)
		geomean = statistics.geometric_mean(speedups)
		if geomean < _MIN_GEOMEAN_SPEEDUP:
			return CheckResult.failure(
				f"TPP/ATen geometric-mean speedup {geomean:.3g} is below {_MIN_GEOMEAN_SPEEDUP:.3g}"
			)
		return CheckResult.success(
			f"all {len(WORKLOAD_CONFIGS)} workloads passed; TPP beat ATen on "
			f"{wins}/{len(speedups)} with {geomean:.2f}x geometric-mean speedup"
		)
