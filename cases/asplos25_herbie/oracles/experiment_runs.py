from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import CommandCheck, PathCheck, PathKind


_START_ERROR_SIMILARITY = 0.90
_END_ERROR_SIMILARITY = 0.50


def _load_results_json(path: Path) -> dict[str, Any]:
	"""Load and validate a Herbie results.json file."""
	text = path.read_text("utf-8")
	data = json.loads(text)
	if not isinstance(data, dict) or "tests" not in data:
		raise ValueError("results.json must be an object with a 'tests' array")
	if not isinstance(data["tests"], list):
		raise ValueError("'tests' must be a list")
	return data


def _discover_herbie_report(repo_root: Path) -> tuple[Path, Path] | None:
	"""Find the best Herbie report directory under the workspace root."""
	best: tuple[int, Path, Path] | None = None
	for results_path in repo_root.glob("*/results.json"):
		report_dir = results_path.parent
		if report_dir.name.startswith("."):
			continue

		html_path: Path | None = None
		for html_name in ("report.html", "index.html"):
			candidate_html = report_dir / html_name
			if candidate_html.is_file():
				html_path = candidate_html
				break
		if html_path is None:
			continue

		try:
			data = _load_results_json(results_path)
		except (OSError, json.JSONDecodeError, ValueError):
			continue

		tests = data["tests"]
		if not tests:
			continue

		if best is None or len(tests) > best[0]:
			best = (len(tests), html_path, results_path)

	if best is None:
		return None
	return best[1], best[2]


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultsJSONStructureCheck(utils.BaseCheck):
	"""Fail if results.json is missing or has invalid structure."""

	results_path: Path

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		try:
			data = _load_results_json(self.results_path)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return utils.CheckResult.failure(f"cannot read or parse {self.results_path}: {exc}")

		tests = data["tests"]
		if not tests:
			return utils.CheckResult.failure("results.json contains no tests")

		required_fields = {"start", "end", "status", "time", "name"}
		for i, test in enumerate(tests):
			if not isinstance(test, dict):
				return utils.CheckResult.failure(f"tests[{i}] is not an object")
			missing = required_fields - test.keys()
			if missing:
				return utils.CheckResult.failure(
					f"tests[{i}] ({test.get('name', '?')}) missing fields: {sorted(missing)}"
				)

		return utils.CheckResult.success(
			message=f"results.json has {len(tests)} tests with valid structure"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class HerbieNoRegressionCheck(utils.BaseCheck):
	"""Fail if any test has end > start (accuracy regression)."""

	results_path: Path

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		try:
			data = _load_results_json(self.results_path)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return utils.CheckResult.failure(f"cannot load results: {exc}")

		violations: list[str] = []
		for test in data["tests"]:
			start = test.get("start")
			end = test.get("end")
			if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
				continue
			if end > start:
				violations.append(f"{test.get('name', '?')}: end={end:.4f} > start={start:.4f}")

		if violations:
			detail = "\n".join(f"- {v}" for v in violations[:10])
			more = f"\n... ({len(violations) - 10} more)" if len(violations) > 10 else ""
			return utils.CheckResult.failure(
				f"{len(violations)} test(s) regressed (end > start):\n{detail}{more}"
			)

		return utils.CheckResult.success(
			message=f"all {len(data['tests'])} tests satisfy end <= start"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkCountCheck(utils.BaseCheck):
	"""Fail if the results have fewer tests than the reference."""

	results_path: Path
	reference_path: Path

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		try:
			results = _load_results_json(self.results_path)
			reference = _load_results_json(self.reference_path)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return utils.CheckResult.failure(f"cannot load results: {exc}")

		actual = len(results["tests"])
		expected = len(reference["tests"])

		if actual < expected:
			return utils.CheckResult.failure(
				f"results have {actual} tests, reference has {expected}"
			)

		return utils.CheckResult.success(
			message=f"results have {actual} tests (reference: {expected})"
		)


def _extract_values(
	results_path: Path,
	reference_path: Path,
	field: str,
) -> tuple[list[float], list[float]]:
	results = _load_results_json(results_path)
	reference = _load_results_json(reference_path)

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
class HerbieValueSimilarityCheck(utils.BaseCheck):
	"""Pearson similarity of a numeric field against reference."""

	results_path: Path
	reference_path: Path
	field: str
	threshold: float

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		try:
			observed, reference = _extract_values(
				self.results_path,
				self.reference_path,
				self.field,
			)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return utils.CheckResult.failure(f"cannot extract {self.field}: {exc}")

		if len(observed) < 2:
			return utils.CheckResult.failure(
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
		return delegated.check()


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self._workspace_dir
		report = _discover_herbie_report(repo_root)
		reference_path = self.ref_path("results.ref.json")

		checks: list[utils.BaseCheck] = []

		if report is None:
			checks.append(
				PathCheck(
					name="results_json",
					path=repo_root / "graphs" / "results.json",
					kind=PathKind.FILE,
				)
			)
			return tuple(checks)

		html_path, results_path = report
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
