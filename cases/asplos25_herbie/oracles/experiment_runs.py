from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathCheck, PathKind
from evaluator.oracles.oracle_checks_runtime import OraclePath, RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck, CheckResult


def _load_results_json(path: OraclePath, *, executor: RuntimeCheckExecutor) -> dict[str, Any]:
	"""Load and validate a Herbie results.json file."""
	text = executor.read_file_text(path)
	data = json.loads(text)
	if not isinstance(data, dict) or "tests" not in data:
		raise ValueError("results.json must be an object with a 'tests' array")
	if not isinstance(data["tests"], list):
		raise ValueError("'tests' must be a list")
	return data


def _discover_herbie_report(
	repo_root: Path, reference_path: OraclePath, *, executor: RuntimeCheckExecutor
) -> tuple[OraclePath, OraclePath] | None:
	"""Find the newest report for the pinned Hamming benchmark suite."""
	reference = _load_results_json(reference_path, executor=executor)
	# The bundled report uses zero iterations, so only its test names and commit are comparable.
	expected_names = {test["name"] for test in reference["tests"]}
	best: tuple[float, OraclePath, OraclePath] | None = None
	for results_path in executor.glob(repo_root, "*/results.json"):
		report_dir = results_path.parent
		if report_dir.name.startswith("."):
			continue

		html_path: OraclePath | None = None
		for html_name in ("report.html", "index.html"):
			candidate_html = RuntimePath.from_parts((report_dir / html_name).as_posix())
			if executor.path_is_file(candidate_html):
				html_path = candidate_html
				break
		if html_path is None:
			continue

		try:
			runtime_results_path = RuntimePath.from_parts(results_path.as_posix())
			data = _load_results_json(runtime_results_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError):
			continue

		tests = data["tests"]
		if (
			data.get("commit") != reference.get("commit")
			or len(tests) != len(expected_names)
			or any(not isinstance(test, dict) for test in tests)
			or {test.get("name") for test in tests} != expected_names
		):
			continue

		date = data.get("date")
		if not isinstance(date, (int, float)):
			continue
		if best is None or date > best[0]:
			best = (date, html_path, runtime_results_path)

	if best is None:
		return None
	return best[1], best[2]


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultsJSONStructureCheck(BaseCheck):
	"""Fail if results.json is missing or has invalid structure."""

	results_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			data = _load_results_json(self.results_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return CheckResult.failure(f"cannot read or parse {self.results_path}: {exc}")

		tests = data["tests"]
		if not tests:
			return CheckResult.failure("results.json contains no tests")

		required_fields = {"start", "end", "status", "time", "name"}
		for i, test in enumerate(tests):
			if not isinstance(test, dict):
				return CheckResult.failure(f"tests[{i}] is not an object")
			missing = required_fields - test.keys()
			if missing:
				return CheckResult.failure(
					f"tests[{i}] ({test.get('name', '?')}) missing fields: {sorted(missing)}"
				)

		return CheckResult.success(
			message=f"results.json has {len(tests)} tests with valid structure"
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class HerbieAccuracyImprovementCheck(BaseCheck):
	"""Require the pinned benchmark suite to improve without regressions."""

	results_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			data = _load_results_json(self.results_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return CheckResult.failure(f"cannot load results: {exc}")

		violations: list[str] = []
		improved = 0
		for test in data["tests"]:
			if not isinstance(test, dict):
				return CheckResult.failure("report contains a non-object test entry")
			start = test.get("start")
			end = test.get("end")
			if (
				not isinstance(start, (int, float))
				or not isinstance(end, (int, float))
				or not math.isfinite(start)
				or not math.isfinite(end)
			):
				return CheckResult.failure(f"{test.get('name', '?')}: nonnumeric error value")
			if end > start:
				violations.append(f"{test.get('name', '?')}: end={end:.4f} > start={start:.4f}")
			elif end < start:
				improved += 1

		if violations:
			detail = "\n".join(f"- {v}" for v in violations[:10])
			more = f"\n... ({len(violations) - 10} more)" if len(violations) > 10 else ""
			return CheckResult.failure(
				f"{len(violations)} test(s) regressed (end > start):\n{detail}{more}"
			)

		if improved <= len(data["tests"]) / 2:
			return CheckResult.failure(
				f"only {improved}/{len(data['tests'])} tests improved; expected a majority"
			)

		return CheckResult.success(message=f"{improved}/{len(data['tests'])} tests improved without regressions")


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkCountCheck(BaseCheck):
	"""Fail if the results have fewer tests than the reference."""

	results_path: OraclePath
	reference_path: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		try:
			results = _load_results_json(self.results_path, executor=executor)
			reference = _load_results_json(self.reference_path, executor=executor)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return CheckResult.failure(f"cannot load results: {exc}")

		actual = len(results["tests"])
		expected = len(reference["tests"])

		if actual < expected:
			return CheckResult.failure(
				f"results have {actual} tests, reference has {expected}"
			)

		return CheckResult.success(
			message=f"results have {actual} tests (reference: {expected})"
		)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self._workspace_dir
		reference_path = self.ref_path("results.ref.json")
		report = _discover_herbie_report(repo_root, reference_path, executor=self.executor)

		checks: list[BaseCheck] = []

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
			HerbieAccuracyImprovementCheck(
				name="accuracy_improvement",
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

		return tuple(checks)
