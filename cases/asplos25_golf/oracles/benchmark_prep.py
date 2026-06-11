from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind, checks, utils
from evaluator.oracles.utils import BaseCheck, CheckResult, RuntimeCheckExecutor

from .consts import (
	BENCHMARKS_REF,
	CORRECT_PATH,
	DEADLOCK_CGO_PATH,
	DEADLOCK_GOKER_PATH,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryContainsTestCases(BaseCheck):
	"""Fail if fewer than min_count subdirectories contain main.go ."""

	path: Path
	min_count: int
	executor: RuntimeCheckExecutor | None = dataclasses.field(default=None, repr=False, compare=False)

	def check(self) -> CheckResult:
		if not utils.check_path_is_dir(self.path, executor=self.executor):
			return CheckResult.failure(f"directory does not exist: {self.path}")

		if self.executor is not None:
			result = self.executor.run_process_capture(
				cmd=["find", ".", "-name", "main.go", "-type", "f"],
				cwd=self.path,
				env=None,
				timeout_seconds=30.0,
			)
			if result.timed_out:
				return CheckResult.failure(f"timed out scanning {self.path}")
			if result.returncode != 0:
				return CheckResult.failure(f"failed to scan {self.path}: {result.stderr}")
			count = sum(1 for line in result.stdout.splitlines() if line.strip())
		else:
			count = 0
			try:
				for entry in self.path.rglob("main.go"):
					if entry.is_file():
						count += 1
			except OSError as exc:
				return CheckResult.failure(f"failed to scan {self.path}: {exc}")

		if count < self.min_count:
			return CheckResult.failure(
				f"found {count} test case(s) with main.go in {self.path}, "
				f"expected at least {self.min_count}"
			)

		return CheckResult.success(message=f"found {count} test case(s) in {self.path.name}")


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		ref = json.loads(self.ref_path(BENCHMARKS_REF).read_text(encoding="utf-8"))

		return (
			self.path_check(
				name="deadlock_goker_exists",
				path=self.app_path(DEADLOCK_GOKER_PATH),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="deadlock_cgo_examples_exists",
				path=self.app_path(DEADLOCK_CGO_PATH),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="correct_tests_exist",
				path=self.app_path(CORRECT_PATH),
				kind=PathKind.DIRECTORY,
			),
            self.direcoty_glob_count_check(
				name="goker_suite_has_tests",
				directory=self.app_path(DEADLOCK_GOKER_PATH),
				min_count=ref["deadlock/goker"],
                pattern="*/main.go",
			),
            self.direcoty_glob_count_check(
				name="cgo_examples_has_tests",
				directory=self.app_path(DEADLOCK_CGO_PATH),
				min_count=ref["deadlock/cgo-examples"],
                pattern="*/main.go",
			),
            self.direcoty_glob_count_check(
				name="correct_suite_has_tests",
				directory=self.app_path(CORRECT_PATH),
				min_count=ref["correct"],
                pattern="*/main.go",
			),
		)
