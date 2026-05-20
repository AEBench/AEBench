from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.discovery import experiment_runs
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.experiment_runs_checks import ElementwiseSimilarityThresholdCheck
from evaluator.oracles.utils import Checkable
from models import OracleInput


def _load_ground_truth(path: Path) -> dict[tuple[str, str], set[str]]:
	try:
		lines = path.read_text(encoding="utf-8").splitlines()
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
) -> dict[tuple[str, str], set[str]]:
	observed: dict[tuple[str, str], set[str]] = {}
	for csv_path in results_root.rglob("*.csv"):
		try:
			with csv_path.open("r", encoding="utf-8", newline="") as handle:
				reader = csv.reader(handle)
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


@experiment_runs
def oracle_experiment_runs(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	results_root = repo_root / "results"
	truth_path = context.case_dir / "refs" / "bugs_ground_truth.csv"

	def _check_ground_truth_coverage() -> utils.CheckResult:
		try:
			truth = _load_ground_truth(truth_path)
			location_to_benchmark = {
				(bug_type, location): benchmark
				for (bug_type, benchmark), locations in truth.items()
				for location in locations
			}
			observed = _load_observed(
				results_root,
				location_to_benchmark=location_to_benchmark,
			)
		except ValueError as exc:
			return utils.CheckResult.failure(str(exc))

		buckets = sorted(truth)
		reference_counts = [float(len(truth[bucket])) for bucket in buckets]
		observed_counts = [
			float(len(observed.get(bucket, set()) & truth[bucket])) for bucket in buckets
		]
		result = ElementwiseSimilarityThresholdCheck(
			name="ground_truth_coverage_by_bucket",
			observed=observed_counts,
			reference=reference_counts,
			threshold=0.75,
		).check()
		if result.ok:
			matched = int(sum(observed_counts))
			total = int(sum(reference_counts))
			return utils.CheckResult.success(f"matched {matched}/{total} benchmark bug signatures")
		return result

	return (
		FilesystemPathCheck(
			name="results_root_exists",
			path=results_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="ground_truth_csv_exists",
			path=truth_path,
			path_type=PathType.FILE,
		),
		utils.Check(
			name="ground_truth_coverage_by_bucket",
			fn=_check_ground_truth_coverage,
		),
	)
