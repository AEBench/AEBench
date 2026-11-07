from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, PathCheck, PathKind, reporting
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck
from models import OracleInput

from .common import find_eval_dir, missing_dir_check, run_in_runtime

_Buckets = dict[tuple[str, str], set[str]]

# The min percentege of bugs we expect to reproduce
_COVERAGE_THRESHOLD = 0.75
# Ignore benchmarks built with gradle -- they require manual intervention
_UNSCORED_BENCHMARKS = frozenset({"cassandra", "elasticsearch"})


# Search "[Injection] ... | Retry location ---<location>--- |" line in a test log.
def _find_injected_locations(results_root: str, *wanted: str) -> list[str]:
	import pathlib
	import re

	location = re.compile(rb"Retry location ---(.*?)---")
	found = set()
	for log in pathlib.Path(results_root).glob("**/test-reports/*-output.txt"):
		with open(log, "rb") as f:
			for line in f:
				if b"[Injection]" in line and (match := location.search(line)):
					found.add(match.group(1).decode(errors="replace"))
	return sorted(found & set(wanted))


def _load_ground_truth(
	path: Path, *, executor: RuntimeCheckExecutor
) -> dict[tuple[str, str], set[str]]:
	try:
		lines = executor.read_file_text(path).splitlines()
	except OSError as exc:
		raise ValueError(f"failed to read ground-truth CSV: {exc}") from exc

	buckets: dict[tuple[str, str], set[str]] = {}
	for index, line in enumerate(lines, start=1):
		stripped = line.strip()
		if not stripped:
			continue
		try:
			benchmark, bug_type, location = stripped.split(",", 2)
		except ValueError as exc:
			raise ValueError(f"invalid ground-truth row #{index}: {line!r}") from exc
		key = (bug_type.strip(), benchmark.strip())
		buckets.setdefault(key, set()).add(location.strip())
	return buckets


def _load_observed(
	results_root: Path,
	*,
	location_to_benchmark: dict[tuple[str, str], str],
	executor: RuntimeCheckExecutor,
) -> dict[tuple[str, str], set[str]]:
	observed: dict[tuple[str, str], set[str]] = {}
	for csv_path in executor.glob(results_root, "**/*.csv"):
		try:
			text = executor.read_file_text(RuntimePath.from_parts(csv_path.as_posix()))
			reader = csv.reader(io.StringIO(text))
			for row in reader:
				if len(row) < 3:
					continue
				line = ",".join(row)
				if ("how-bug" not in line) and ("when-missing-" not in line):
					continue
				bug_type = row[1].strip()
				location = row[2].strip()
				benchmark = location_to_benchmark.get((bug_type, location))
				if benchmark is None:
					continue
				key = (bug_type, benchmark)
				observed.setdefault(key, set()).add(location)
		except OSError as exc:
			raise ValueError(f"failed to read result CSV {csv_path}: {exc}") from exc
		except csv.Error as exc:
			raise ValueError(f"invalid CSV in {csv_path}: {exc}") from exc
	return observed


def _injected_locations(
	results_root: RuntimePath, locations: set[str], *, executor: RuntimeCheckExecutor
) -> set[str]:
	"""Returns the ``locations`` that a raw WASABI test log shows faults injected at."""
	if not locations:
		return set()
	try:
		found = run_in_runtime(
			executor,
			_find_injected_locations,
			str(executor.resolve_path(results_root)),
			*sorted(locations),
			timeout_seconds=900.0,
		)
	except RuntimeError as exc:
		raise ValueError(f"failed to scan test logs under {results_root}: {exc}") from exc
	return set(found)


def oracle_experiment_runs(
	context: OracleInput, executor: RuntimeCheckExecutor
) -> Sequence[BaseCheck]:
	truth_path = context.case_dir / "refs" / "bugs_ground_truth.csv"
	results_root, searched = find_eval_dir(executor, context.workspace_dir, "results")
	if results_root is None:
		return (missing_dir_check("results_root_exists", searched),)
	cache: dict[str, tuple[_Buckets, _Buckets, set[str]]] = {}

	def _load(executor: RuntimeCheckExecutor) -> tuple[_Buckets, _Buckets, set[str]]:
		if "results" not in cache:
			truth = _load_ground_truth(truth_path, executor=executor)
			location_to_benchmark = {
				(bug_type, location): benchmark
				for (bug_type, benchmark), locations in truth.items()
				for location in locations
			}
			observed = _load_observed(
				results_root,
				location_to_benchmark=location_to_benchmark,
				executor=executor,
			)
			claimed = set().union(*observed.values())
			injected = _injected_locations(results_root, claimed, executor=executor)
			cache["results"] = (truth, observed, claimed - injected)
		return cache["results"]

	def _make_coverage_check(name: str, *, scored: bool) -> reporting.Check:
		def _check(executor: RuntimeCheckExecutor) -> reporting.CheckResult:
			try:
				truth, observed, unbacked = _load(executor)
			except ValueError as exc:
				return reporting.CheckResult.failure(str(exc))

			buckets = sorted(
				(bucket for bucket in truth if (bucket[1] not in _UNSCORED_BENCHMARKS) == scored),
				key=lambda bucket: (bucket[1], bucket[0]),
			)
			lines = []
			matched = total = 0
			ignored: set[str] = set()
			for bug_type, benchmark in buckets:
				claimed = observed.get((bug_type, benchmark), set()) & truth[bug_type, benchmark]
				found = claimed - unbacked
				ignored |= claimed & unbacked
				matched += len(found)
				total += len(truth[bug_type, benchmark])
				lines.append(
					f"{benchmark}/{bug_type}: {len(found)}/{len(truth[bug_type, benchmark])}"
				)
			if ignored:
				lines.append(
					"ignored, no [Injection] line in test-reports: " + ", ".join(sorted(ignored))
				)
			message = f"matched {matched}/{total} benchmark bug signatures\n" + "\n".join(lines)
			if matched >= _COVERAGE_THRESHOLD * total:
				return reporting.CheckResult.success(message)
			return reporting.CheckResult.failure(
				f"coverage below {_COVERAGE_THRESHOLD:.0%}: {message}"
			)

		return reporting.Check(name=name, optional=not scored, fn=_check)

	return (
		PathCheck(
			name="results_root_exists",
			path=results_root,
			kind=PathKind.DIRECTORY,
		),
		PathCheck(
			name="ground_truth_csv_exists",
			path=truth_path,
			kind=PathKind.FILE,
		),
		_make_coverage_check("ground_truth_coverage", scored=True),
		_make_coverage_check("unscored_benchmark_coverage", scored=False),
	)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return oracle_experiment_runs(self.context, self.executor)
