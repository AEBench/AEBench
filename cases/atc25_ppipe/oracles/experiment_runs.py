from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles.experiment_runs_checks import (
	ListSimilarityCheck,
	SimilarityMetric,
)

from evaluator.oracles import utils

from .common import (
	GlobFileExistsCheck,
	NonEmptyDirectoryCheck,
)

_PLAN_XPUT_SIMILARITY = 0.95

_LOGS_CSV_REQUIRED_COLUMNS = {
	"dnn",
	"dnn_group_id",
	"dnn_id",
	"slo",
	"gpu_models",
	"gpu_counts",
	"bw",
	"xput",
	"lf",
	"scheduler",
	"perc_dropped",
	"perc_violate_sla",
}


def _extract_plan_xputs(plan_dir: Path) -> list[tuple[str, float]]:
	"""Extract (filename, xput) pairs from plan JSONs in a directory."""
	results: list[tuple[str, float]] = []
	for json_path in sorted(plan_dir.glob("*.json")):
		try:
			with json_path.open("utf-8") as f:
				plans = json.load(f)
		except (OSError, json.JSONDecodeError):
			continue
		if not isinstance(plans, list):
			continue
		for i, plan in enumerate(plans):
			if isinstance(plan, dict) and isinstance(plan.get("xput"), (int, float)):
				results.append((f"{json_path.name}[{i}]", float(plan["xput"])))
	return results


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanThroughputCorrelationCheck(utils.BaseCheck):
	"""Pearson similarity of MILP plan throughputs against reference."""

	output_dir: Path
	reference_dir: Path
	threshold: float

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		try:
			output_xputs = _extract_plan_xputs(self.output_dir)
			ref_xputs = _extract_plan_xputs(self.reference_dir)
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot read plan directories: {exc}")

		if not ref_xputs:
			return utils.CheckResult.failure(f"no reference plans found in {self.reference_dir}")
		if not output_xputs:
			return utils.CheckResult.failure(f"no output plans found in {self.output_dir}")

		ref_by_key = dict(ref_xputs)
		observed: list[float] = []
		reference: list[float] = []
		for key, xput in output_xputs:
			if key in ref_by_key:
				observed.append(xput)
				reference.append(ref_by_key[key])

		if len(observed) < 2:
			return utils.CheckResult.failure(
				f"only {len(observed)} matching plan(s) between output and reference "
				f"(output: {len(output_xputs)}, reference: {len(ref_xputs)})"
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


@dataclass(frozen=True, slots=True, kw_only=True)
class LogsCSVStructureCheck(utils.BaseCheck):
	"""Fail if logs.csv is missing required columns or has too few rows."""

	path: Path
	min_rows: int = 10

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		if not self.path.is_file():
			return utils.CheckResult.failure(f"file missing: {self.path}")

		try:
			text = self.path.read_text("utf-8")
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot read {self.path}: {exc}")

		lines = [line for line in text.strip().splitlines() if line.strip()]
		if len(lines) < 2:
			return utils.CheckResult.failure(
				f"{self.path.name} has {len(lines)} line(s), expected header + data"
			)

		reader = csv.reader(lines)
		header = [col.strip() for col in next(reader)]
		missing = _LOGS_CSV_REQUIRED_COLUMNS - set(header)
		if missing:
			return utils.CheckResult.failure(f"{self.path.name} missing columns: {sorted(missing)}")

		data_rows = len(lines) - 1
		if data_rows < self.min_rows:
			return utils.CheckResult.failure(
				f"{self.path.name} has {data_rows} data row(s), expected at least {self.min_rows}"
			)

		return utils.CheckResult.success(message=f"{self.path.name}: {data_rows} rows, columns OK")


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir

		outputs = repo_root / "outputs"
		refs_plans = self.ref_path("plans")

		reqs: list[utils.BaseCheck] = [
			NonEmptyDirectoryCheck(
				name="prepartition_mappings_dir",
				path=outputs / "prepartition_mappings",
			),
			GlobFileExistsCheck(
				name="prepartition_mappings_csv",
				directory=outputs / "prepartition_mappings",
				pattern="*.csv",
			),
		]

		for workload in ("maf19", "maf21", "ablation"):
			output_plan_dir = outputs / "plans" / workload
			ref_plan_dir = refs_plans / workload

			reqs.append(
				NonEmptyDirectoryCheck(
					name=f"plans_{workload}_dir",
					path=output_plan_dir,
				)
			)
			if ref_plan_dir.is_dir():
				reqs.append(
					PlanThroughputCorrelationCheck(
						name=f"plans_{workload}_xput_correlation",
						output_dir=output_plan_dir,
						reference_dir=ref_plan_dir,
						threshold=_PLAN_XPUT_SIMILARITY,
					)
				)

		reqs.append(
			LogsCSVStructureCheck(
				name="cluster_logs_maf19",
				path=outputs / "cluster-logs" / "maf19" / "logs.csv",
			)
		)
		reqs.append(
			LogsCSVStructureCheck(
				name="cluster_logs_maf21",
				path=outputs / "cluster-logs" / "maf21" / "logs.csv",
			)
		)
		reqs.append(
			LogsCSVStructureCheck(
				name="cluster_logs_ablation",
				path=outputs / "cluster-logs" / "ablation_maf19" / "logs.csv",
			)
		)

		for fig in ("fig6", "fig7", "fig8", "fig10"):
			reqs.append(
				GlobFileExistsCheck(
					name=f"figure_{fig}_output",
					directory=outputs,
					pattern=f"*{fig}*",
				)
			)

		return tuple(reqs)
