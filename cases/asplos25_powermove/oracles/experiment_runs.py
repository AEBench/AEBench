from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .consts import WORKLOADS
from .parsing import PowerMoveEvaluationCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			PowerMoveEvaluationCheck(
				name="powermove_evaluation",
				workloads={
					workload: (
						qubits,
						self.runtime_path("data", f"{family}_no_storage_compare.txt"),
						self.runtime_path("data", f"{family}_storage_compare.txt"),
						self.runtime_path("logs", f"{family}_no_storage_compare.log"),
						self.runtime_path("logs", f"{family}_storage_compare.log"),
					)
					for workload, (family, qubits, _) in WORKLOADS.items()
				},
				reference_path=self.ref_path("evaluation.ref.json"),
			),
		)
