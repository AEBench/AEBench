from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.utils import BaseCheck

from .consts import CASE_STUDY_ISLE_FILES, EXPERIMENT_SCRIPTS


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Verify the experiment inputs (case-study ISLE files and scripts) are present."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = []

		for rel in CASE_STUDY_ISLE_FILES:
			checks.append(
				self.path_check(
					name=f"isle_{rel}",
					path=self.app_path(rel),
					kind=PathKind.FILE,
				)
			)

		for rel in EXPERIMENT_SCRIPTS:
			checks.append(
				self.path_check(
					name=f"script_{rel}",
					path=self.app_path(rel),
					kind=PathKind.FILE,
				)
			)

		return tuple(checks)
