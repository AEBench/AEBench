from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathCheck, PathKind
from evaluator.oracles.checks import (
	ListSimilarityCheck,
	SimilarityMetric,
)


_PLAN_XPUT_SIMILARITY = 0.95

_REQUIRED_WORKLOADS = ("maf19", "maf21", "ablation")

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
	"""Extract (filename[index], xput) pairs from plan JSON files in a directory."""
	if not plan_dir.is_dir():
		raise OSError(f"plan directory missing: {plan_dir}")

	results: list[tuple[str, float]] = []
	errors: list[str] = []

	for json_path in sorted(plan_dir.glob("*.json")):
		try:
			with json_path.open("r", encoding="utf-8") as handle:
				plans = json.load(handle)
		except (OSError, json.JSONDecodeError) as exc:
			errors.append(f"{json_path.name}: {exc}")
			continue

		if not isinstance(plans, list):
			errors.append(f"{json_path.name}: expected top-level list")
			continue

		for index, plan in enumerate(plans):
			if isinstance(plan, dict) and isinstance(plan.get("xput"), int | float):
				results.append((f"{json_path.name}[{index}]", float(plan["xput"])))

	if errors:
		raise ValueError("; ".join(errors[:5]))

	return results


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryGlobCountCheck(utils.BaseCheck):
	"""Fail if fewer than min_count entries match the glob pattern."""

	directory: Path
	pattern: str
	min_count: int = 1

	def check(self) -> utils.CheckResult:
		if not self.directory.is_dir():
			return utils.CheckResult.failure(f"directory missing: {self.directory}")

		try:
			matches = list(self.directory.glob(self.pattern))
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot scan {self.directory}: {exc}")

		if len(matches) < self.min_count:
			return utils.CheckResult.failure(
				f"found {len(matches)} entr(y/ies) matching {self.pattern!r} in "
				f"{self.directory}, expected at least {self.min_count}"
			)

		return utils.CheckResult.success(
			message=(
				f"{len(matches)} entr(y/ies) matching {self.pattern!r} "
				f"in {self.directory}"
			)
		)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanThroughputCorrelationCheck(utils.BaseCheck):
	"""Pearson similarity of MILP plan throughputs against reference."""

	output_dir: Path
	reference_dir: Path
	threshold: float

	def check(self) -> utils.CheckResult:
		try:
			output_xputs = _extract_plan_xputs(self.output_dir)
			ref_xputs = _extract_plan_xputs(self.reference_dir)
		except (OSError, ValueError) as exc:
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

	def check(self) -> utils.CheckResult:
		if not self.path.is_file():
			return utils.CheckResult.failure(f"file missing: {self.path}")

		try:
			with self.path.open("r", encoding="utf-8", newline="") as handle:
				reader = csv.DictReader(handle)
				header = reader.fieldnames or []
				rows = list(reader)
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot read {self.path}: {exc}")
		except csv.Error as exc:
			return utils.CheckResult.failure(f"cannot parse {self.path}: {exc}")

		if not header:
			return utils.CheckResult.failure(f"{self.path.name} is missing a CSV header")

		missing = _LOGS_CSV_REQUIRED_COLUMNS - set(header)
		if missing:
			return utils.CheckResult.failure(
				f"{self.path.name} missing columns: {sorted(missing)}"
			)

		data_rows = len(rows)
		if data_rows < self.min_rows:
			return utils.CheckResult.failure(
				f"{self.path.name} has {data_rows} data row(s), expected at least {self.min_rows}"
			)

		return utils.CheckResult.success(
			message=f"{self.path.name}: {data_rows} rows, columns OK"
		)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.workspace_path()
		outputs = repo_root / "outputs"
		refs_plans = self.ref_path("plans")

		reqs: list[utils.BaseCheck] = [
			PathCheck(
				name="outputs_dir_exists",
				path=outputs,
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="reference_plans_dir_exists",
				path=refs_plans,
				kind=PathKind.DIRECTORY,
			),
			DirectoryGlobCountCheck(
				name="prepartition_mappings_dir_populated",
				directory=outputs / "prepartition_mappings",
				pattern="*",
				min_count=1,
			),
			DirectoryGlobCountCheck(
				name="prepartition_mappings_csv",
				directory=outputs / "prepartition_mappings",
				pattern="*/*.csv",
				min_count=1,
			),
		]

		for workload in _REQUIRED_WORKLOADS:
			output_plan_dir = outputs / "plans" / workload
			ref_plan_dir = refs_plans / workload

			reqs.extend(
				(
					PathCheck(
						name=f"plans_{workload}_dir",
						path=output_plan_dir,
						kind=PathKind.DIRECTORY,
					),
					PathCheck(
						name=f"reference_plans_{workload}_dir",
						path=ref_plan_dir,
						kind=PathKind.DIRECTORY,
					),
					PlanThroughputCorrelationCheck(
						name=f"plans_{workload}_xput_correlation",
						output_dir=output_plan_dir,
						reference_dir=ref_plan_dir,
						threshold=_PLAN_XPUT_SIMILARITY,
					),
				)
			)

		reqs.extend(
			(
				LogsCSVStructureCheck(
					name="cluster_logs_maf19",
					path=outputs / "cluster-logs" / "maf19" / "logs.csv",
				),
				LogsCSVStructureCheck(
					name="cluster_logs_maf21",
					path=outputs / "cluster-logs" / "maf21" / "logs.csv",
				),
				LogsCSVStructureCheck(
					name="cluster_logs_ablation",
					path=outputs / "cluster-logs" / "ablation_maf19" / "logs.csv",
				),
			)
		)

		for fig in ("fig6", "fig7", "fig8", "fig10"):
			reqs.append(
				DirectoryGlobCountCheck(
					name=f"figure_{fig}_output",
					directory=outputs,
					pattern=f"*{fig}*",
					min_count=1,
				)
			)

		return tuple(reqs)