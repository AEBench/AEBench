"""Benchmark prep oracle (always passes)."""

from typing import Sequence

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		return []
