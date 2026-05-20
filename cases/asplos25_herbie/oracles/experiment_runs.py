from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.experiment_runs_checks import (
	ListSimilarityCheck,
	SimilarityMetric,
)

from evaluator.oracles import utils

_START_ERROR_SIMILARITY = 0.90
_END_ERROR_SIMILARITY = 0.50


def _load_results_json(path: Path) -> dict:
	"""Load and validate a Herbie results.json file."""
	text = path.read_text("utf-8")
	data = json.loads(text)
	if not isinstance(data, dict) or "tests" not in data:
		raise ValueError("results.json must be an object with a 'tests' array")
	if not isinstance(data["tests"], list):
		raise ValueError("'tests' must be a list")
	return data


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultsJSONStructureCheck(utils.BaseCheck):
	"""Fail if results.json is missing or has invalid structure."""

	results_path: Path

	def check(self, *_args, **_kwargs) -> utils.CheckResult:
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

	def check(self, *_args, **_kwargs) -> utils.CheckResult:
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

	def check(self, *_args, **_kwargs) -> utils.CheckResult:
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

	def check(self, *_args, **_kwargs) -> utils.CheckResult:
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
		repo_root = self.paths.workspace_dir

		results_path: Path | None = None
		for output_dir_name in ("graphs", "report"):
			candidate = repo_root / output_dir_name / "results.json"
			if candidate.is_file():
				results_path = candidate
				break

		reference_path = self.ref_path("results.ref.json")

		checks: list[utils.BaseCheck] = []

		for output_dir_name in ("graphs", "report"):
			index = repo_root / output_dir_name / "index.html"
			if index.is_file():
				checks.append(
					FilesystemPathCheck(
						name=f"{output_dir_name}_index_html",
						path=index,
						path_type=PathType.FILE,
					)
				)
				break
		else:
			checks.append(
				FilesystemPathCheck(
					name="graphs_index_html",
					path=repo_root / "graphs" / "index.html",
					path_type=PathType.FILE,
				)
			)

		if results_path is None:
			checks.append(
				FilesystemPathCheck(
					name="results_json",
					path=repo_root / "graphs" / "results.json",
					path_type=PathType.FILE,
				)
			)
			return tuple(checks)

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
