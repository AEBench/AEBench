from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .consts import WORKLOAD_CONFIGS
from .parsing import EinsumEvaluationCheck

_EVALUATION_LOG_DIR = "evaluation/logs"


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			EinsumEvaluationCheck(
				name="einsum_tree_evaluation",
				logs={
					workload: self.runtime_path(_EVALUATION_LOG_DIR, f"{workload}.log")
					for workload, _ in WORKLOAD_CONFIGS
				},
				reference_path=self.ref_path("workloads.ref.json"),
				executor=self.executor,
			),
		)
