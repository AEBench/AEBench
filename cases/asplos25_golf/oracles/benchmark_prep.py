from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	BENCHMARKS_REF,
	CORRECT_PATH,
	DEADLOCK_CGO_PATH,
	DEADLOCK_GOKER_PATH,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		ref = json.loads(self.ref_path(BENCHMARKS_REF).read_text(encoding="utf-8"))

		return (
			self.path_check(
				name="deadlock_goker_exists",
				path=self.runtime_path(DEADLOCK_GOKER_PATH),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="deadlock_cgo_examples_exists",
				path=self.runtime_path(DEADLOCK_CGO_PATH),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="correct_tests_exist",
				path=self.runtime_path(CORRECT_PATH),
				kind=PathKind.DIRECTORY,
			),
			self.min_matching_entry_count_check(
				name="goker_suite_has_tests",
				directory=self.runtime_path(DEADLOCK_GOKER_PATH),
				min_count=ref["deadlock/goker"],
				pattern="**/main.go",
			),
			self.min_matching_entry_count_check(
				name="cgo_examples_has_tests",
				directory=self.runtime_path(DEADLOCK_CGO_PATH),
				min_count=ref["deadlock/cgo-examples"],
				pattern="**/main.go",
			),
			self.min_matching_entry_count_check(
				name="correct_suite_has_tests",
				directory=self.runtime_path(CORRECT_PATH),
				min_count=ref["correct"],
				pattern="**/main.go",
			),
		)
