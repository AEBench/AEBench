from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, PathCheck, PathKind
from evaluator.oracles.reporting import BaseCheck, CheckResult

_MIN_DETECTION_RATE = 90.0
_MIN_EXPECTED_GO_INSTRUCTIONS = 121

_RESULT_TEST_DIR_FALL_BACK = "tester"


def _find_result_file(repo_root: Path, filename: str) -> Path:
	"""Find a result file in the repo root or tester/ subdirectory."""
	candidate = repo_root / filename
	if candidate.is_file():
		return candidate
	else:
		return repo_root / _RESULT_TEST_DIR_FALL_BACK / filename


def _parse_aggregated_total(results_text: str) -> float | None:
	"""Return the Aggregated/Total detection rate, or None."""
	for line in results_text.splitlines():
		stripped = line.strip()
		if stripped.startswith("Aggregated"):
			percentages = re.findall(r"(\d+(?:\.\d+)?)%", stripped)
			if percentages:
				return float(percentages[-1])
	return None


def _count_total_go_instructions(results_text: str) -> int | None:
	"""Count benchmark rows plus the Remaining line."""
	in_aggregated = False
	row_count = 0
	remaining_count = 0

	for line in results_text.splitlines():
		stripped = line.strip()

		if stripped.startswith("Benchmark\t"):
			in_aggregated = True
			continue

		if not in_aggregated:
			continue

		if stripped.startswith("Aggregated"):
			break

		remaining_match = re.match(r"Remaining\s+(\d+)\s+go\s+instruction", stripped)
		if remaining_match:
			remaining_count = int(remaining_match.group(1))
			continue

		if "\t" in stripped and stripped:
			row_count += 1

	if not in_aggregated:
		return None
	return row_count + remaining_count


def _has_cgo_examples_entries(results_text: str) -> bool:
	"""True if the aggregated report contains cgo-examples entries."""
	in_aggregated = False
	for line in results_text.splitlines():
		stripped = line.strip()
		if stripped.startswith("Benchmark\t"):
			in_aggregated = True
			continue
		if not in_aggregated:
			continue
		if stripped.startswith("Aggregated"):
			break
		if "cgo-examples" in stripped:
			return True
	return False


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregatedDetectionRateCheck(BaseCheck):
	"""Fail if the aggregated detection rate is below the threshold."""

	results_path: Path
	min_rate: float

	def check(self) -> CheckResult:
		try:
			text = self.results_path.read_text(encoding="utf-8")
		except OSError as exc:
			return CheckResult.failure(f"failed to read results file {self.results_path}: {exc}")

		rate = _parse_aggregated_total(text)
		if rate is None:
			return CheckResult.failure(
				"could not parse Aggregated/Total detection rate from results file"
			)

		if rate < self.min_rate:
			return CheckResult.failure(
				f"aggregated detection rate {rate:.2f}% is below minimum {self.min_rate:.2f}%"
			)

		return CheckResult.success(message=f"aggregated detection rate: {rate:.2f}%")


@dataclass(frozen=True, slots=True, kw_only=True)
class NoCgoExamplesCheck(BaseCheck):
	"""Fail if the aggregated report lists cgo-examples entries."""

	results_path: Path

	def check(self) -> CheckResult:
		try:
			text = self.results_path.read_text(encoding="utf-8")
		except OSError as exc:
			return CheckResult.failure(f"failed to read results file {self.results_path}: {exc}")

		if _has_cgo_examples_entries(text):
			return CheckResult.failure(
				"aggregated report contains cgo-examples entries (expected only goker entries)"
			)

		return CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class TotalGoInstructionsCheck(BaseCheck):
	"""Fail if the total go instruction count is less than  expected."""

	results_path: Path
	expected_count: int

	def check(self) -> CheckResult:
		try:
			text = self.results_path.read_text(encoding="utf-8")
		except OSError as exc:
			return CheckResult.failure(f"failed to read results file {self.results_path}: {exc}")

		count = _count_total_go_instructions(text)
		if count is None:
			return CheckResult.failure("could not parse go instruction count from results file")

		if count < self.expected_count:
			return CheckResult.failure(
				f"total go instructions {count} < expected {self.expected_count}"
			)

		return CheckResult.success(message=f"total go instructions: {count}")


@dataclass(frozen=True, slots=True, kw_only=True)
class PerfCSVStructureCheck(BaseCheck):
	"""Fail if the performance CSV is missing expected columns."""

	csv_path: Path

	_EXPECTED_COLUMNS = (
		"Target",
		"GC cycles",
		"Mark clock OFF",
		"Mark clock ON",
		"CPU utilization OFF",
		"CPU utilization ON",
	)

	def check(self) -> CheckResult:
		try:
			text = self.csv_path.read_text(encoding="utf-8").strip()
		except OSError as exc:
			return CheckResult.failure(f"failed to read CSV file {self.csv_path}: {exc}")

		if not text:
			return CheckResult.failure("CSV file is empty")

		try:
			rows = list(csv.reader(io.StringIO(text)))
		except csv.Error as exc:
			return CheckResult.failure(f"CSV parse error: {exc}")

		if len(rows) < 2:
			return CheckResult.failure(
				f"CSV has {len(rows)} row(s), expected at least 2 (header + data)"
			)

		header_normalized = []
		for col in rows[0]:
			normalized = col.strip().split("(")[0].strip().replace("\u03bc", "u")
			header_normalized.append(normalized)

		missing = [
			expected
			for expected in self._EXPECTED_COLUMNS
			if not any(expected.lower() in h.lower() for h in header_normalized)
		]

		if missing:
			return CheckResult.failure(f"CSV missing expected columns: {missing}; found: {rows[0]}")

		return CheckResult.success(
			message=f"CSV has {len(rows) - 1} data rows with expected columns"
		)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		results_path = _find_result_file(self.artifact_path(), "results")
		perf_csv_path = _find_result_file(self.artifact_path(), "results-perf.csv")
		tex_path = _find_result_file(self.artifact_path(), "results.tex")
		tex_fallback = self.artifact_path("results.tex")

		return (
			PathCheck(
				name="results_file_exists",
				path=self.artifact_path("results"),
				kind=PathKind.FILE,
			),
			AggregatedDetectionRateCheck(
				name="rq1a_aggregated_detection_rate",
				results_path=results_path,
				min_rate=_MIN_DETECTION_RATE,
			),
			NoCgoExamplesCheck(
				name="rq1a_no_cgo_examples",
				results_path=results_path,
			),
			TotalGoInstructionsCheck(
				name="rq1a_total_go_instructions",
				results_path=results_path,
				expected_count=_MIN_EXPECTED_GO_INSTRUCTIONS,
			),
			PathCheck(
				name="results_perf_csv_exists",
				path=self.artifact_path("results-perf.csv"),
				kind=PathKind.FILE,
			),
			PerfCSVStructureCheck(
				name="rq2_perf_csv_structure",
				csv_path=perf_csv_path,
			),
			PathCheck(
				name="rq2_boxplot_tex_exists",
				path=tex_path or tex_fallback,
				kind=PathKind.FILE,
			),
		)
