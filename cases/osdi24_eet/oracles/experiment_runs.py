from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.discovery import experiment_runs
from evaluator.oracles.experiment_runs_checks import ElementwiseSimilarityThresholdCheck
from evaluator.oracles.utils import Checkable
from models import OracleInput


def _load_expected_counts(path: Path) -> dict[str, int]:
	try:
		raw = json.loads(path.read_text(encoding="utf-8"))
	except OSError as exc:
		raise ValueError(f"failed to read expected bug counts: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid expected bug counts JSON: {exc}") from exc

	if not isinstance(raw, dict):
		raise ValueError(f"expected bug counts JSON must be an object, got {type(raw).__name__}")

	counts: dict[str, int] = {}
	for benchmark, value in raw.items():
		if not isinstance(benchmark, str) or not benchmark.strip():
			raise ValueError(f"invalid benchmark name in expected bug counts: {benchmark!r}")
		if not isinstance(value, int):
			raise ValueError(f"expected bug count for {benchmark!r} must be an integer")
		counts[benchmark] = value
	return counts


def _count_bug_dirs(path: Path) -> int:
	if not path.is_dir():
		return 0
	try:
		return sum(1 for entry in path.iterdir() if entry.is_dir())
	except OSError:
		return 0


@experiment_runs
def oracle_experiment_runs(context: OracleInput) -> Sequence[Checkable]:
	expected_path = context.case_dir / "refs" / "bugs_expected.json"
	observed_path = context.output_dir / "bugs_observed.json"

	def _compare_bug_totals() -> utils.CheckResult:
		try:
			expected = _load_expected_counts(expected_path)
		except ValueError as exc:
			return utils.CheckResult.failure(str(exc))

		benchmarks = list(expected.keys())
		observed = {
			benchmark: _count_bug_dirs(context.workspace_dir / f"{benchmark}_test" / "bugs")
			for benchmark in benchmarks
		}

		try:
			observed_path.parent.mkdir(parents=True, exist_ok=True)
			observed_path.write_text(
				json.dumps(observed, indent=2, sort_keys=True) + "\n",
				encoding="utf-8",
			)
		except OSError as exc:
			return utils.CheckResult.failure(f"failed to write observed bug totals: {exc}")

		result = ElementwiseSimilarityThresholdCheck(
			name="bugs_totals_match",
			observed=[float(observed[benchmark]) for benchmark in benchmarks],
			reference=[float(expected[benchmark]) for benchmark in benchmarks],
			threshold=1.0,
		).check()
		if result.ok:
			return utils.CheckResult.success(
				f"bug totals match refs; wrote observed totals to {observed_path}"
			)
		return utils.CheckResult.failure(
			f"{result.message}\nobserved totals written to {observed_path}"
		)

	return (
		utils.Check(
			name="bugs_totals_match",
			fn=_compare_bug_totals,
		),
	)
