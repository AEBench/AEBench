"""Artifact build oracle (always passes)"""

from typing import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleArtifactBuildBase


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		return []
