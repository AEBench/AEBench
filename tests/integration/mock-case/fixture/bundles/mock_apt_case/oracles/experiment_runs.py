"""Experiment runs oracle (always passes)."""

from typing import Sequence

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleExperimentRunsBase


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		return []
