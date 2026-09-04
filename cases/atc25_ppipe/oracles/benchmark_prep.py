from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathCheck, PathKind
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

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
class LFSFileResolvedCheck(BaseCheck):
	"""Fail if the file is a Git LFS pointer instead of real data."""

	path: Path
	min_size: int = _LFS_POINTER_MAX_BYTES

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		if not executor.path_is_file(self.path):
			return CheckResult.failure(f"file missing: {self.path}")

		try:
			result = executor.run_process_capture(
				cmd=("wc", "-c", str(executor.resolve_path(self.path))),
				cwd=None,
				env=None,
				timeout_seconds=10.0,
			)
			size = int(result.stdout.split()[0]) if result.returncode == 0 else -1
		except (OSError, ValueError, IndexError) as exc:
			return CheckResult.failure(f"cannot determine size of {self.path}: {exc}")

		if size < 0:
			return CheckResult.failure(f"cannot determine size of {self.path}")

		if size <= self.min_size:
			try:
				head = executor.read_file_text(self.path)[:64].encode()
			except OSError as exc:
				return CheckResult.failure(
					f"{self.path.name} is unexpectedly small ({size} bytes) "
					f"and could not be read to check for a Git LFS pointer: {exc}"
				)

			if head.startswith(b"version https://git-lfs.github.com"):
				return CheckResult.failure(
					f"{self.path.name} is a Git LFS pointer ({size} bytes). "
					f"Run 'git lfs pull' to download the actual data."
				)

			return CheckResult.failure(f"{self.path.name} is unexpectedly small ({size} bytes)")

		return CheckResult.success(message=f"{self.path.name}: {size} bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelListCountCheck(BaseCheck):
	"""Fail if model_list.txt has fewer than expected entries."""

	path: Path
	expected_count: int

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		if not executor.path_is_file(self.path):
			return CheckResult.failure(f"file missing: {self.path}")

		try:
			lines = [
				line.strip()
				for line in executor.read_file_text(self.path).splitlines()
				if line.strip()
			]
		except OSError as exc:
			return CheckResult.failure(f"cannot read {self.path}: {exc}")

		if len(lines) < self.expected_count:
			return CheckResult.failure(
				f"model_list.txt has {len(lines)} entries, expected at least {self.expected_count}"
			)

		return CheckResult.success(message=f"model_list.txt has {len(lines)} model(s)")


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()
		data_dir = repo_root / "data"

		checks: list[BaseCheck] = [
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
				optional=True,
			),
			ModelListCountCheck(
				name="model_list_count",
				path=data_dir / "model_list.txt",
				expected_count=_EXPECTED_MODELS,
				optional=True,
			),
		]

		for subdir in _REQUIRED_MODEL_SUBDIRS:
			checks.append(
				self.min_matching_entry_count_check(
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
			self.min_matching_entry_count_check(
				name="prepartition_mappings",
				directory=data_dir / "prepartition_mappings",
				pattern="*/*.csv",
				min_count=_MIN_PREPARTITION_CSVS,
			)
		)

		for plan_dir in _REQUIRED_PLAN_DIRS:
			checks.append(
				self.min_matching_entry_count_check(
					name=f"reference_plans_{plan_dir}",
					directory=data_dir / "plans" / plan_dir,
					pattern="*.json",
					min_count=_MIN_PLAN_JSONS_PER_WORKLOAD,
				)
			)

		return tuple(checks)
