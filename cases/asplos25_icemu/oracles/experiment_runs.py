from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .checks import (
	EvaluationCheck,
	ExecutedNotebooksCheck,
	ResultSetCheck,
	ResultsParseableCheck,
)
from .consts import (
	LOGS_DIR,
	NOTEBOOK_OUTPUTS,
	PLOT_OUTPUTS,
	RESULTS_REF,
	expected_result_names,
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the full experiment matrix and the paper-facing evaluation outputs."""

	def requirements(self) -> Sequence[BaseCheck]:
		expected_names = expected_result_names()
		checks: list[BaseCheck] = [
			ResultSetCheck(
				name="complete_result_matrix",
				logs_dir=self.runtime_path(LOGS_DIR),
				expected_names=expected_names,
			),
			ResultsParseableCheck(
				name="all_results_parseable",
				logs_dir=self.runtime_path(LOGS_DIR),
				expected_names=expected_names,
			),
			EvaluationCheck(
				name="evaluation_metrics",
				logs_dir=self.runtime_path(LOGS_DIR),
				reference_path=self.ref_path(RESULTS_REF),
			),
			ExecutedNotebooksCheck(
				name="executed_notebooks",
				paths=tuple(
					(Path(relative).name, self.runtime_path(relative))
					for relative in NOTEBOOK_OUTPUTS
				),
			),
		]
		checks.extend(
			self.path_check(
				name=f"output_{Path(relative).stem.replace('-', '_')}",
				path=self.runtime_path(relative),
				kind=PathKind.FILE,
			)
			for relative in PLOT_OUTPUTS
		)
		return tuple(checks)
