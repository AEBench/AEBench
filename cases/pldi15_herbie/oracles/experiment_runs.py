from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import ListSimilarityCheck, PathCheck, PathKind, SimilarityMetric
from evaluator.oracles.oracle_checks_runtime import OraclePath, RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

_START_ERROR_SIMILARITY = 0.90
_END_ERROR_SIMILARITY = 0.50


def _load_results_json(path: OraclePath, *, executor: RuntimeCheckExecutor) -> dict[str, Any]:
	"""Load and validate a Herbie results.json file."""
	text = executor.read_file_text(path)
	data = json.loads(text)
	if not isinstance(data, dict) or "tests" not in data:
		raise ValueError("results.json must be an object with a 'tests' array")
	if not isinstance(data["tests"], list) or not data["tests"]:
		raise ValueError("'tests' must be a non-empty list")
	names: set[str] = set()
	for i, test in enumerate(data["tests"]):
		if not isinstance(test, dict):
			raise ValueError(f"tests[{i}] is not an object")
		status = test.get("status")
		if not isinstance(status, str) or status not in {
			"imp-start",
			"ex-start",
			"eq-start",
			"eq-target",
			"gt-target",
			"gt-start",
			"uni-start",
			"lt-target",
			"lt-start",
			"apx-start",
		}:
			raise ValueError(f"tests[{i}] did not complete successfully")
		name = test.get("name")
		if not isinstance(name, str) or not name.strip() or name in names:
			raise ValueError(f"tests[{i}] has a missing or duplicate benchmark name")
		names.add(name)
		for field in ("start", "end", "time"):
			value = test.get(field)
			if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
				raise ValueError(f"tests[{i}].{field} must be a finite non-negative number")
	return data


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultsJSONStructureCheck(BaseCheck):
	"""Fail if results.json is missing or has invalid structure."""

	results_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			data = _load_results_json(self.results_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError, OverflowError) as exc:
			return CheckResult.failure(f"cannot read or parse {self.results_path}: {exc}")

		iterations = data.get("iterations")
		if type(iterations) is not int or iterations != 4:
			return CheckResult.failure("results.json must record four optimization iterations")
		if data.get("seed") != "286210900":
			return CheckResult.failure("results.json must record seed 286210900")

		tests = data["tests"]

		return CheckResult.success(
			message=f"results.json has {len(tests)} tests with valid structure"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class HerbieNoRegressionCheck(BaseCheck):
	"""Require no regressions and at least one strict accuracy improvement."""

	results_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			data = _load_results_json(self.results_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError, OverflowError) as exc:
			return CheckResult.failure(f"cannot load results: {exc}")

		violations: list[str] = []
		for test in data["tests"]:
			start = test.get("start")
			end = test.get("end")
			if end > start:
				violations.append(f"{test.get('name', '?')}: end={end:.4f} > start={start:.4f}")

		if violations:
			detail = "\n".join(f"- {v}" for v in violations[:10])
			more = f"\n... ({len(violations) - 10} more)" if len(violations) > 10 else ""
			return CheckResult.failure(
				f"{len(violations)} test(s) regressed (end > start):\n{detail}{more}"
			)

		if not any(test["end"] < test["start"] for test in data["tests"]):
			return CheckResult.failure("no benchmark improved accuracy (all end == start)")

		return CheckResult.success(
			message=f"all {len(data['tests'])} tests satisfy end <= start, with strict improvement"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkCountCheck(BaseCheck):
	"""Require exactly the reference benchmark names, each present once."""

	results_path: OraclePath
	reference_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			results = _load_results_json(self.results_path, executor=executor)
			reference = _load_results_json(self.reference_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError, OverflowError) as exc:
			return CheckResult.failure(f"cannot load results: {exc}")

		actual = len(results["tests"])
		expected = len(reference["tests"])

		actual_names = {test["name"] for test in results["tests"]}
		expected_names = {test["name"] for test in reference["tests"]}
		if actual_names != expected_names:
			return CheckResult.failure(
				f"benchmark mismatch: missing={sorted(expected_names - actual_names)}, "
				f"unexpected={sorted(actual_names - expected_names)}"
			)

		return CheckResult.success(message=f"results have {actual} tests (reference: {expected})")


def _extract_values(
	results_path: OraclePath,
	reference_path: OraclePath,
	field: str,
	*,
	executor: RuntimeCheckExecutor,
) -> tuple[list[float], list[float]]:
	results = _load_results_json(results_path, executor=executor)
	reference = _load_results_json(reference_path, executor=executor)

	ref_by_name: dict[str, float] = {}
	for test in reference["tests"]:
		name = test.get("name", "")
		value = test.get(field)
		if isinstance(value, (int, float)) and name:
			ref_by_name[name] = float(value)

	observed: list[float] = []
	ref_values: list[float] = []
	for test in results["tests"]:
		name = test.get("name", "")
		value = test.get(field)
		if name in ref_by_name and isinstance(value, (int, float)):
			observed.append(float(value))
			ref_values.append(ref_by_name[name])

	return observed, ref_values


@dataclass(frozen=True, slots=True, kw_only=True)
class HerbieValueSimilarityCheck(BaseCheck):
	"""Pearson similarity of a numeric field against reference."""

	results_path: OraclePath
	reference_path: OraclePath
	field: str
	threshold: float

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			observed, reference = _extract_values(
				self.results_path,
				self.reference_path,
				self.field,
				executor=executor,
			)
		except (OSError, json.JSONDecodeError, ValueError, OverflowError) as exc:
			return CheckResult.failure(f"cannot extract {self.field}: {exc}")

		if len(observed) < 2:
			return CheckResult.failure(
				f"too few matching tests for {self.field} correlation "
				f"(found {len(observed)}, need at least 2)"
			)

		delegated = ListSimilarityCheck(
			name=self.name,
			optional=self.optional,
			observed=observed,
			reference=reference,
			metric=SimilarityMetric.PEARSON,
			min_similarity=self.threshold,
		)
		return delegated.check(executor)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self._workspace_dir
		reference_path = self.ref_path("results.ref.json")

		# The upstream www/ directory contains a bundled report, not this run's output.
		html_path = repo_root / "graphs" / "index.html"
		results_path = repo_root / "graphs" / "results.json"

		checks: list[BaseCheck] = []
		checks.append(
			PathCheck(
				name="herbie_report_html",
				path=html_path,
				kind=PathKind.FILE,
			)
		)

		checks.append(
			ResultsJSONStructureCheck(
				name="results_json_structure",
				results_path=results_path,
			)
		)

		checks.append(
			HerbieNoRegressionCheck(
				name="no_accuracy_regression",
				results_path=results_path,
			)
		)

		checks.append(
			BenchmarkCountCheck(
				name="benchmark_count",
				results_path=results_path,
				reference_path=reference_path,
			)
		)

		checks.append(
			HerbieValueSimilarityCheck(
				name="start_error_correlation",
				results_path=results_path,
				reference_path=reference_path,
				field="start",
				threshold=_START_ERROR_SIMILARITY,
			)
		)

		checks.append(
			HerbieValueSimilarityCheck(
				name="end_error_correlation",
				results_path=results_path,
				reference_path=reference_path,
				field="end",
				threshold=_END_ERROR_SIMILARITY,
			)
		)

		return tuple(checks)
