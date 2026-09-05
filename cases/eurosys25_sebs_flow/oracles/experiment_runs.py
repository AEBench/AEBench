from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	check_read_file_text,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_RUN_NAMES = (
	"cold_starts",
	"trip_booking_overhead",
	"storage_overhead",
	"invocation_latency",
)
_RESULTS_PATH = "evaluation/results.json"
_LOG_PATH = "evaluation/analysis.log"
_FIGURE_PATHS = (
	"evaluation/figures/overhead-trip-booking.pdf",
	"evaluation/figures/overhead-storage.pdf",
	"evaluation/figures/invocation-latency.pdf",
)
_RESULT_REL_TOL = 1e-5
_RESULT_ABS_TOL = 1e-6

JsonObject: TypeAlias = Mapping[str, object]


def _read_json_object(path: OraclePath, executor: RuntimeCheckExecutor) -> JsonObject:
	payload = json.loads(check_read_file_text(path, executor=executor))
	if not isinstance(payload, dict):
		raise ValueError("top-level JSON value must be an object")
	return payload


def _compare_results(
	observed: object,
	reference: object,
	*,
	label: str,
	errors: list[str],
) -> int:
	if isinstance(reference, dict):
		if not isinstance(observed, dict):
			errors.append(f"{label}: expected an object")
			return 0
		missing = sorted(set(reference) - set(observed))
		if missing:
			errors.append(f"{label}: missing {missing}")
			return 0
		return sum(
			_compare_results(observed[key], value, label=f"{label}.{key}", errors=errors)
			for key, value in reference.items()
		)

	if isinstance(reference, int | float) and not isinstance(reference, bool):
		if not isinstance(observed, int | float) or isinstance(observed, bool):
			errors.append(f"{label}: expected a number")
			return 0
		observed_value = float(observed)
		reference_value = float(reference)
		if not math.isfinite(observed_value):
			errors.append(f"{label}: non-finite value {observed!r}")
			return 0
		if not math.isclose(
			observed_value,
			reference_value,
			rel_tol=_RESULT_REL_TOL,
			abs_tol=_RESULT_ABS_TOL,
		):
			errors.append(f"{label}: got {observed_value}, expected {reference_value}")
		return 1

	if observed != reference:
		errors.append(f"{label}: got {observed!r}, expected {reference!r}")
	return 1


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisResultsCheck(BaseCheck):
	observed_path: OraclePath
	reference_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			observed = _read_json_object(self.observed_path, executor)
			reference = _read_json_object(self.reference_path, executor)
		except (OSError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
			return CheckResult.failure(f"could not read analysis results: {exc}")

		errors: list[str] = []
		metric_count = _compare_results(observed, reference, label="results", errors=errors)
		if errors:
			return CheckResult.failure("; ".join(errors[:8]))
		return CheckResult.success(
			f"{metric_count} fixed-corpus metrics match reference tolerances"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisLogCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			text = check_read_file_text(self.path, executor=executor)
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"could not read analysis log: {exc}")

		position = 0
		for run_name in _RUN_NAMES:
			begin = f"===== BEGIN {run_name} ====="
			end = f"===== END {run_name} status=0 ====="
			begin_at = text.find(begin, position)
			end_at = text.find(end, begin_at + len(begin))
			if begin_at < 0 or end_at < 0:
				return CheckResult.failure(f"analysis.log: missing ordered markers for {run_name}")
			position = end_at + len(end)

		lower = text.lower()
		for forbidden in ("traceback (most recent call last)", "timed out"):
			if forbidden in lower:
				return CheckResult.failure(f"analysis.log contains failure marker {forbidden!r}")
		return CheckResult.success(
			f"all {len(_RUN_NAMES)} scoped analysis runs completed successfully"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfOutputCheck(BaseCheck):
	path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			data = executor.read_file_text(self.path, encoding="latin-1")
		except (OSError, RuntimeError, ValueError) as exc:
			return CheckResult.failure(f"could not read PDF output: {exc}")
		if len(data) < 1024:
			return CheckResult.failure(f"PDF output is only {len(data)} bytes")
		if not data.startswith("%PDF-"):
			return CheckResult.failure("PDF output has no PDF header")
		if "%%EOF" not in data[-1024:]:
			return CheckResult.failure("PDF output has no EOF marker")
		return CheckResult.success(f"valid PDF output ({len(data)} bytes)")


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			AnalysisLogCheck(
				name="scoped_analysis_log",
				path=self.runtime_path(_LOG_PATH),
			),
			AnalysisResultsCheck(
				name="fixed_corpus_results",
				observed_path=self.runtime_path(_RESULTS_PATH),
				reference_path=self.ref_path("analysis.ref.json"),
			),
		]

		for rel_path in _FIGURE_PATHS:
			checks.append(
				PdfOutputCheck(
					name=f"pdf_{Path(rel_path).stem.replace('-', '_')}",
					path=self.runtime_path(rel_path),
				)
			)
		return tuple(checks)
