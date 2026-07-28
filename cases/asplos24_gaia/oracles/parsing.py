from __future__ import annotations

from dataclasses import dataclass, field

from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_SUMMARY_HEADER = "carbon_cost,dollar_cost"


def _parse_summary(text: str) -> tuple[float, float]:
	"""Parse a GAIA summary CSV (`carbon_cost,dollar_cost` + one data row).

	Raises:
		ValueError: If the header or single numeric data row is malformed.
	"""
	rows = [line.strip() for line in text.splitlines() if line.strip()]
	if len(rows) < 2:
		raise ValueError(f"expected header + 1 data row, got {len(rows)} non-empty line(s)")
	if rows[0].replace(" ", "") != _SUMMARY_HEADER:
		raise ValueError(f"unexpected header {rows[0]!r}, want {_SUMMARY_HEADER!r}")
	fields = rows[1].split(",")
	if len(fields) != 2:
		raise ValueError(f"expected 2 values in data row, got {len(fields)}: {rows[1]!r}")
	try:
		return float(fields[0]), float(fields[1])
	except ValueError as exc:
		raise ValueError(f"non-numeric values in data row {rows[1]!r}: {exc}") from exc


def _read_summary(
	path: OraclePath,
	executor: RuntimeCheckExecutor | None,
) -> tuple[float, float]:
	"""Read and parse a summary CSV through the target executor."""
	text = check_read_file_text(path, executor=executor)
	return _parse_summary(text)


def _within_rel_tol(observed: float, expected: float, rel_tol: float) -> bool:
	"""Relative tolerance with an absolute floor for near-zero references."""
	return abs(observed - expected) <= max(abs(expected) * rel_tol, 1e-9)


@dataclass(frozen=True, slots=True, kw_only=True)
class GaiaSummaryCheck(BaseCheck):
	"""Validate one run's summary CSV: exists, well-formed, and numerically
	reproduces the reference carbon_cost / dollar_cost within relative tolerance.
	"""

	path: OraclePath
	filename: str
	expected_carbon: float
	expected_dollar: float
	rel_tol: float
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			carbon, dollar = _read_summary(self.path, self.executor)
		except OSError as exc:
			return CheckResult.failure(f"{self.filename}: could not read file: {exc}")
		except ValueError as exc:
			return CheckResult.failure(f"{self.filename}: malformed summary CSV: {exc}")

		errors: list[str] = []
		if not _within_rel_tol(carbon, self.expected_carbon, self.rel_tol):
			errors.append(
				f"carbon_cost {carbon} != expected {self.expected_carbon} "
				f"(rel_tol {self.rel_tol})"
			)
		if not _within_rel_tol(dollar, self.expected_dollar, self.rel_tol):
			errors.append(
				f"dollar_cost {dollar} != expected {self.expected_dollar} "
				f"(rel_tol {self.rel_tol})"
			)

		if errors:
			return CheckResult.failure(f"{self.filename}: " + "; ".join(errors))

		return CheckResult.success(
			message=f"{self.filename}: carbon_cost {carbon}, dollar_cost {dollar} match reference"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class CarbonReductionCheck(BaseCheck):
	"""Paper claim (Fig 8/9): every carbon-aware policy's carbon_cost is strictly
	below the carbon-agnostic ("No Jobs Wait") baseline.
	"""

	baseline_path: OraclePath
	baseline_label: str
	aware_paths: tuple[tuple[str, OraclePath], ...]
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			baseline_carbon, _ = _read_summary(self.baseline_path, self.executor)
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"baseline {self.baseline_label}: {exc}")

		errors: list[str] = []
		for label, path in self.aware_paths:
			try:
				carbon, _ = _read_summary(path, self.executor)
			except (OSError, ValueError) as exc:
				errors.append(f"{label}: {exc}")
				continue
			if not carbon < baseline_carbon:
				errors.append(
					f"{label}: carbon_cost {carbon} not below baseline {baseline_carbon}"
				)

		if errors:
			return CheckResult.failure("carbon-aware reduction violated: " + "; ".join(errors))

		return CheckResult.success(
			message=(
				f"all {len(self.aware_paths)} carbon-aware policies reduce carbon "
				f"below baseline {baseline_carbon}"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReservedCostReductionCheck(BaseCheck):
	"""Paper claim (Fig 11): allocating reserved instances lowers total dollar
	cost. Every reserved run (r>0) in the carbon-cost / cst_average sweep must
	cost less than the no-reserved (r=0) run.

	Note: the cost curve is U-shaped, not monotonic -- beyond a sweet spot, idle
	reserved capacity you still pay for pushes cost back up -- so this asserts the
	reduction-vs-baseline takeaway rather than a monotonic decrease. The exact
	shape of the curve is pinned separately by the per-run numeric checks.
	"""

	baseline_label: str
	baseline_path: OraclePath
	steps: tuple[tuple[str, OraclePath], ...]
	executor: RuntimeCheckExecutor | None = field(default=None)

	def check(self) -> CheckResult:
		try:
			_, baseline = _read_summary(self.baseline_path, self.executor)
		except (OSError, ValueError) as exc:
			return CheckResult.failure(f"baseline {self.baseline_label}: {exc}")

		errors: list[str] = []
		for label, path in self.steps:
			try:
				_, dollar = _read_summary(path, self.executor)
			except (OSError, ValueError) as exc:
				errors.append(f"{label}: {exc}")
				continue
			if not dollar < baseline:
				errors.append(
					f"{label}: dollar_cost {dollar} not below no-reserved baseline {baseline}"
				)

		if errors:
			return CheckResult.failure("reserved-cost reduction violated: " + "; ".join(errors))

		return CheckResult.success(
			message=(
				f"all {len(self.steps)} reserved allocations cost less than the "
				f"no-reserved baseline {baseline}"
			)
		)
