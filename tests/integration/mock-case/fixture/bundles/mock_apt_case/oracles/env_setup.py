"""Environment setup oracle (always passes)."""

from typing import Sequence

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []
