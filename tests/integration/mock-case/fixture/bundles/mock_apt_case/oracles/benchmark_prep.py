"""Benchmark prep oracle (always passes)."""

from typing import Sequence

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.reporting import BaseCheck


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []
