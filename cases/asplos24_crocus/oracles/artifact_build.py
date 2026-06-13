from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind

from .case_constants import EXPECTED_OUTPUT_PATH


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence:
        return (
            self.path_check(
                name="output_directory_exists",
                path=self.workspace_path(EXPECTED_OUTPUT_PATH).parent,
                kind=PathKind.DIRECTORY,
            ),
        )
