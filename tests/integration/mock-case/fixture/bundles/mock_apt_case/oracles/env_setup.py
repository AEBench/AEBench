"""Environment setup oracle (always passes)."""

from typing import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		return []
