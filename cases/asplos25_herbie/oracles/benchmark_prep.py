from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from evaluator.oracles import utils

_log = logging.getLogger(__name__)

_MIN_BENCH_FPCORE_FILES = 30
_MIN_HAMMING_FPCORE_FILES = 4


@dataclass(frozen=True, slots=True, kw_only=True)
class FPCoreBenchmarkCheck(utils.BaseCheck):
	"""Fail if fewer than min_count valid .fpcore files are found."""

	path: Path
	min_count: int

	def check(self, *_args, **_kwargs) -> utils.CheckResult:
		if not self.path.is_dir():
			return utils.CheckResult.failure(f"benchmark directory does not exist: {self.path}")

		count = 0
		try:
			for fpcore_file in self.path.rglob("*.fpcore"):
				if not fpcore_file.is_file():
					continue
				try:
					head = fpcore_file.read_text("utf-8", errors="replace")[:1024]
					if "FPCore" in head:
						count += 1
				except OSError as exc:
					_log.warning("skipping %s: %s", fpcore_file, exc)
		except OSError as exc:
			return utils.CheckResult.failure(f"failed to scan {self.path}: {exc}")

		if count < self.min_count:
			return utils.CheckResult.failure(
				f"found {count} valid .fpcore file(s) in {self.path}, "
				f"expected at least {self.min_count}"
			)

		return utils.CheckResult.success(
			message=f"found {count} FPCore benchmark file(s) in {self.path.name}"
		)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir

		bench_dir = repo_root / "bench"
		hamming_dir = bench_dir / "hamming"

		return (
			FilesystemPathCheck(
				name="bench_dir_exists",
				path=bench_dir,
				path_type=PathType.DIRECTORY,
			),
			FPCoreBenchmarkCheck(
				name="bench_has_fpcore_files",
				path=bench_dir,
				min_count=_MIN_BENCH_FPCORE_FILES,
			),
			FilesystemPathCheck(
				name="bench_hamming_dir_exists",
				path=hamming_dir,
				path_type=PathType.DIRECTORY,
			),
			FPCoreBenchmarkCheck(
				name="hamming_has_fpcore_files",
				path=hamming_dir,
				min_count=_MIN_HAMMING_FPCORE_FILES,
			),
		)
