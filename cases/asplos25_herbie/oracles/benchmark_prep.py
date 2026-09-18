from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck, CheckResult

_log = logging.getLogger(__name__)

_MIN_BENCH_FPCORE_FILES = 30
_MIN_HAMMING_FPCORE_FILES = 4


@dataclass(frozen=True, slots=True, kw_only=True)
class FPCoreBenchmarkCheck(BaseCheck):
	"""Fail if fewer than min_count valid .fpcore files are found."""

	path: Path
	min_count: int

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		if not executor.path_is_dir(self.path):
			return CheckResult.failure(f"benchmark directory does not exist: {self.path}")

		count = 0
		try:
			for fpcore_file in executor.glob(self.path, "**/*.fpcore"):
				runtime_file = RuntimePath.from_parts(fpcore_file.as_posix())
				if not executor.path_is_file(runtime_file):
					continue
				try:
					head = executor.read_file_text(runtime_file)[:1024]
					if "FPCore" in head:
						count += 1
				except OSError as exc:
					_log.warning("skipping %s: %s", fpcore_file, exc)
		except OSError as exc:
			return CheckResult.failure(f"failed to scan {self.path}: {exc}")

		if count < self.min_count:
			return CheckResult.failure(
				f"found {count} valid .fpcore file(s) in {self.path}, "
				f"expected at least {self.min_count}"
			)

		return CheckResult.success(
			message=f"found {count} FPCore benchmark file(s) in {self.path.name}"
		)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()

		bench_dir = repo_root / "bench"
		hamming_dir = bench_dir / "hamming"

		return (
			self.path_check(
				name="bench_dir_exists",
				path=bench_dir,
				kind=PathKind.DIRECTORY,
			),
			FPCoreBenchmarkCheck(
				name="bench_has_fpcore_files",
				path=bench_dir,
				min_count=_MIN_BENCH_FPCORE_FILES,
			),
			self.path_check(
				name="bench_hamming_dir_exists",
				path=hamming_dir,
				kind=PathKind.DIRECTORY,
			),
			FPCoreBenchmarkCheck(
				name="hamming_has_fpcore_files",
				path=hamming_dir,
				min_count=_MIN_HAMMING_FPCORE_FILES,
			),
		)
