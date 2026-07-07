"""Experiment runs oracle (always passes)."""

from typing import Sequence

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []
