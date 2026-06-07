from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathCheck, PathKind


_LFS_POINTER_MAX_BYTES = 200

_REQUIRED_MODEL_SUBDIRS = (
	"block-timing-tf32",
	"cuts-no-const",
	"model-profile-tf32",
	"node-profile-no-const",
	"shapes",
)

_REQUIRED_PLAN_DIRS = ("maf19", "maf21", "ablation")

_EXPECTED_MODELS = 18
_MIN_PREPARTITION_CSVS = 18
_MIN_PLAN_JSONS_PER_WORKLOAD = 20


@dataclass(frozen=True, slots=True, kw_only=True)
class LFSFileResolvedCheck(utils.BaseCheck):
	"""Fail if the file is a Git LFS pointer instead of real data."""

	path: Path
	min_size: int = _LFS_POINTER_MAX_BYTES

	def check(self) -> utils.CheckResult:
		if not self.path.is_file():
			return utils.CheckResult.failure(f"file missing: {self.path}")

		try:
			size = self.path.stat().st_size
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot stat {self.path}: {exc}")

		if size <= self.min_size:
			try:
				head = self.path.read_bytes()[:64]
			except OSError as exc:
				return utils.CheckResult.failure(
					f"{self.path.name} is unexpectedly small ({size} bytes) "
					f"and could not be read to check for a Git LFS pointer: {exc}"
				)

			if head.startswith(b"version https://git-lfs.github.com"):
				return utils.CheckResult.failure(
					f"{self.path.name} is a Git LFS pointer ({size} bytes). "
					f"Run 'git lfs pull' to download the actual data."
				)

			return utils.CheckResult.failure(
				f"{self.path.name} is unexpectedly small ({size} bytes)"
			)

		return utils.CheckResult.success(message=f"{self.path.name}: {size} bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelListCountCheck(utils.BaseCheck):
	"""Fail if model_list.txt has fewer than expected entries."""

	path: Path
	expected_count: int

	def check(self) -> utils.CheckResult:
		if not self.path.is_file():
			return utils.CheckResult.failure(f"file missing: {self.path}")

		try:
			lines = [
				line.strip()
				for line in self.path.read_text(encoding="utf-8").splitlines()
				if line.strip()
			]
		except OSError as exc:
			return utils.CheckResult.failure(f"cannot read {self.path}: {exc}")

		if len(lines) < self.expected_count:
			return utils.CheckResult.failure(
				f"model_list.txt has {len(lines)} entries, expected at least {self.expected_count}"
			)

		return utils.CheckResult.success(message=f"model_list.txt has {len(lines)} model(s)")


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryGlobCountCheck(utils.BaseCheck):
	"""Fail if fewer than min_count entries match the glob pattern."""

	directory: Path
	pattern: str
	min_count: int

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


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.workspace_path()
		data_dir = repo_root / "data"

		checks: list[utils.BaseCheck] = [
			PathCheck(
				name="data_dir_exists",
				path=data_dir,
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="models_dir_exists",
				path=data_dir / "models",
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="plans_dir_exists",
				path=data_dir / "plans",
				kind=PathKind.DIRECTORY,
			),
			ModelListCountCheck(
				name="model_list_count",
				path=data_dir / "model_list.txt",
				expected_count=_EXPECTED_MODELS,
			),
		]

		for subdir in _REQUIRED_MODEL_SUBDIRS:
			checks.append(
				DirectoryGlobCountCheck(
					name=f"models_{subdir}_populated",
					directory=data_dir / "models" / subdir,
					pattern="*",
					min_count=1,
				)
			)

		checks.append(
			LFSFileResolvedCheck(
				name="maf_trace_not_lfs_pointer",
				path=(
					data_dir
					/ "maf_traces"
					/ "azure_functions_trace_2021"
					/ "AzureFunctionsInvocationTraceForTwoWeeksJan2021.txt"
				),
			)
		)

		checks.append(
			DirectoryGlobCountCheck(
				name="prepartition_mappings",
				directory=data_dir / "prepartition_mappings",
				pattern="*/*.csv",
				min_count=_MIN_PREPARTITION_CSVS,
			)
		)

		for plan_dir in _REQUIRED_PLAN_DIRS:
			checks.append(
				DirectoryGlobCountCheck(
					name=f"reference_plans_{plan_dir}",
					directory=data_dir / "plans" / plan_dir,
					pattern="*.json",
					min_count=_MIN_PLAN_JSONS_PER_WORKLOAD,
				)
			)

		return tuple(checks)