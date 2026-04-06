"""Artifact build oracle for mock_apt_case — always passes."""
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self):
        return []
