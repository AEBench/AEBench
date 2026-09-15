from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .consts import EVALUATION_DIR, WORKLOADS
from .parsing import PowerMoveEvaluationCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			PowerMoveEvaluationCheck(
				name="powermove_evaluation",
				results_dir=self.runtime_path(EVALUATION_DIR, "results"),
				logs_dir=self.runtime_path(EVALUATION_DIR, "logs"),
				reference_path=self.ref_path("evaluation.ref.json"),
				expected_workloads=tuple(WORKLOADS),
			),
		)
