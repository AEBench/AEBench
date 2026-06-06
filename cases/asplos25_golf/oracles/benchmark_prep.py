from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathCheck, PathKind
from evaluator.oracles.utils import BaseCheck, CheckResult

_MIN_GOKER_TESTS = 50
_MIN_CGO_TESTS = 3
_MIN_CORRECT_TESTS = 10


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryContainsTestCases(BaseCheck):
	"""Fail if fewer than min_count subdirectories contain main.go."""

	path: Path
	min_count: int

	def check(self) -> CheckResult:
		if not self.path.is_dir():
			return CheckResult.failure(f"directory does not exist: {self.path}")

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
		repo_root = self.artifact_path()

		tests_dir = repo_root / "tester" / "tests"

		return (
			PathCheck(
				name="tests_deadlock_dir",
				path=tests_dir / "deadlock",
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="tests_correct_dir",
				path=tests_dir / "correct",
				kind=PathKind.DIRECTORY,
			),
			DirectoryContainsTestCases(
				name="goker_suite_has_tests",
				path=tests_dir / "deadlock" / "gobench" / "goker",
				min_count=_MIN_GOKER_TESTS,
			),
			DirectoryContainsTestCases(
				name="cgo_examples_has_tests",
				path=tests_dir / "deadlock" / "cgo-examples",
				min_count=_MIN_CGO_TESTS,
			),
			DirectoryContainsTestCases(
				name="correct_suite_has_tests",
				path=tests_dir / "correct",
				min_count=_MIN_CORRECT_TESTS,
			),
		)
