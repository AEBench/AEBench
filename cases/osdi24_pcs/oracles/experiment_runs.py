from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils

_log = logging.getLogger(__name__)
from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.experiment_runs_checks import (
	ListSimilarityCheck,
	SimilarityMetric,
)


_POLICY_MAP_TO_REF = {
	"AFS": "AFS",
	"SRSF": "Tiresias",
	"THEMIS": "Themis",
	"FIFO": "FIFO",
	"PCS_jct": "PCS-JCT",
	"PCS_bal": "PCS-bal",
	"PCS_pred": "PCS-pred",
}
_POLICIES = tuple(_POLICY_MAP_TO_REF.keys())

_WORKLOAD2_TRACES = ("0e4a51", "ee9e8c")

_TOY_CSVS = tuple(f"new_data/{p}_themis1_result.csv" for p in _POLICIES)
_GAVEL_CSVS = tuple(f"new_data/{p}_gavel_result.csv" for p in _POLICIES)

_JCT_SIMILARITY = 0.85
_ERROR_SIMILARITY = 0.80


def _workload2_csvs() -> tuple[str, ...]:
	paths: list[str] = []
	for trace in _WORKLOAD2_TRACES:
		for policy in _POLICIES:
			paths.append(f"new_data/{policy}_{trace}_result.csv")
	return tuple(paths)


def _parse_raw_csv(path: Path) -> dict[str, float]:
	"""Parse a simulator CSV and compute JCT and prediction error metrics."""
	text = path.read_text("utf-8")
	reader = csv.DictReader(text.strip().splitlines())
	rows = list(reader)
	if not rows:
		raise ValueError(f"empty CSV: {path}")

	jcts: list[float] = []
	errors: list[float] = []

	for row in rows:
		submit = float(row["submit_time"])
		end = float(row["end_time"])
		jct = end - submit
		if jct <= 0:
			continue
		jcts.append(jct)

		est_end = float(row["estimated_end_time"])
		est_start = float(row["estimated_start_time"])
		if est_end == -1 and est_start == -1:
			continue
		pred_jct = est_end - submit
		if pred_jct > 0:
			errors.append(100.0 * abs(pred_jct - jct) / pred_jct)

	if not jcts:
		raise ValueError(f"no valid JCT rows in {path}")

	jcts_sorted = sorted(jcts)
	avg_jct = sum(jcts) / len(jcts)
	p99_idx = min(int(len(jcts_sorted) * 0.99), len(jcts_sorted) - 1)
	p99_jct = jcts_sorted[p99_idx]

	avg_error = 0.0
	p99_error = 0.0
	if errors:
		errors_sorted = sorted(errors)
		avg_error = sum(errors) / len(errors)
		p99_idx_e = min(int(len(errors_sorted) * 0.99), len(errors_sorted) - 1)
		p99_error = errors_sorted[p99_idx_e]

	return {
		"avg_jct": avg_jct,
		"p99_jct": p99_jct,
		"avg_error": avg_error,
		"p99_error": p99_error,
	}


def _load_reference_csv(path: Path) -> dict[str, dict[str, float]]:
	"""Load a reference CSV into {trace: {policy: value}}."""
	text = path.read_text("utf-8")
	reader = csv.DictReader(text.strip().splitlines())
	result: dict[str, dict[str, float]] = {}
	for row in reader:
		trace = row.get("workload", "").strip()
		if not trace:
			continue
		result[trace] = {}
		for col, val in row.items():
			col = col.strip()
			if col == "workload":
				continue
			try:
				result[trace][col] = float(val)
			except (ValueError, TypeError):
				_log.debug(
					"Skipping non-numeric reference value in %s (workload=%s, column=%s, value=%r)",
					path,
					trace,
					col,
					val,
				)
	return result


@dataclass(frozen=True, slots=True, kw_only=True)
class NonEmptyFileCheck(utils.BaseCheck):
	"""Fail if the file is missing or empty."""

	path: Path

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		if not self.path.is_file():
			return utils.CheckResult.failure(f"file missing: {self.path}")
		try:
			if self.path.stat().st_size == 0:
				return utils.CheckResult.failure(f"file is empty: {self.path}")
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot stat {self.path}: {exc}")
		return utils.CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class SimulationMetricCorrelationCheck(utils.BaseCheck):
	"""Pearson similarity of a computed metric against reference."""

	new_data_dir: Path
	reference_path: Path
	traces: tuple[str, ...]
	metric: str
	threshold: float
	normalize_jct: bool = False

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		try:
			ref_data = _load_reference_csv(self.reference_path)
		except (OSError, ValueError) as exc:
			return utils.CheckResult.failure(f"cannot load reference: {exc}")

		observed: list[float] = []
		reference: list[float] = []

		for trace in self.traces:
			if trace not in ref_data:
				continue

			raw_metrics: dict[str, float] = {}
			for policy in _POLICIES:
				csv_path = self.new_data_dir / f"{policy}_{trace}_result.csv"
				if not csv_path.is_file():
					continue
				try:
					metrics = _parse_raw_csv(csv_path)
					raw_metrics[policy] = metrics[self.metric]
				except (OSError, ValueError, KeyError) as exc:
					_log.warning(
						"skipping %s for trace %s: %s: %s",
						policy,
						trace,
						type(exc).__name__,
						exc,
					)
					continue

			if self.normalize_jct and raw_metrics:
				min_val = min(raw_metrics.values())
				if min_val > 0:
					raw_metrics = {p: v / min_val for p, v in raw_metrics.items()}

			for policy, ref_name in _POLICY_MAP_TO_REF.items():
				obs_val = raw_metrics.get(policy)
				ref_val = ref_data.get(trace, {}).get(ref_name)
				if obs_val is not None and ref_val is not None:
					observed.append(obs_val)
					reference.append(ref_val)

		if len(observed) < 3:
			return utils.CheckResult.failure(
				f"too few data points for {self.metric} correlation "
				f"(found {len(observed)}, need at least 3)"
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

		new_data = repo_root / "new_data"
		reqs: list[utils.BaseCheck] = []

		reqs.append(
			FilesystemPathCheck(
				name="new_data_dir",
				path=new_data,
				path_type=PathType.DIRECTORY,
			)
		)

		for csv_rel in _TOY_CSVS:
			reqs.append(
				NonEmptyFileCheck(
					name=f"toy_{Path(csv_rel).stem}",
					path=repo_root / csv_rel,
				)
			)

		for csv_rel in _workload2_csvs():
			reqs.append(
				NonEmptyFileCheck(
					name=f"wl2_{Path(csv_rel).stem}",
					path=repo_root / csv_rel,
				)
			)

		for csv_rel in _GAVEL_CSVS:
			reqs.append(
				NonEmptyFileCheck(
					name=f"wl3_{Path(csv_rel).stem}",
					path=repo_root / csv_rel,
				)
			)

		reqs.append(
			NonEmptyFileCheck(
				name="timing_expt_pkl",
				path=repo_root / "new_data" / "timing_expt.pkl",
			)
		)
		reqs.append(
			NonEmptyFileCheck(
				name="size_error_expt_pkl",
				path=repo_root / "new_data" / "size_error_expt.pkl",
			)
		)

		ref_traces = ("0e4a51", "ee9e8c")

		reqs.append(
			SimulationMetricCorrelationCheck(
				name="avg_jct_correlation",
				new_data_dir=new_data,
				reference_path=self.ref_path("avg_jct_results.csv"),
				traces=ref_traces,
				metric="avg_jct",
				threshold=_JCT_SIMILARITY,
				normalize_jct=True,
			)
		)
		reqs.append(
			SimulationMetricCorrelationCheck(
				name="avg_error_correlation",
				new_data_dir=new_data,
				reference_path=self.ref_path("avg_error_results.csv"),
				traces=ref_traces,
				metric="avg_error",
				threshold=_ERROR_SIMILARITY,
			)
		)
		reqs.append(
			SimulationMetricCorrelationCheck(
				name="p99_error_correlation",
				new_data_dir=new_data,
				reference_path=self.ref_path("p99_error_results.csv"),
				traces=ref_traces,
				metric="p99_error",
				threshold=_ERROR_SIMILARITY,
			)
		)

		return tuple(reqs)
