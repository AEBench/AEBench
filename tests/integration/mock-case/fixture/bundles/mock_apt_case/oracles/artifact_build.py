"""Artifact build oracle (always passes)"""

from typing import Sequence

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []
