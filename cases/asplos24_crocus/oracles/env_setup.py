from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind

from .case_constants import INSTRUCTION_PATH


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence:
        return (
            self.path_check(
                name="instructions_exist",
                path=self.workspace_path(INSTRUCTION_PATH),
                kind=PathKind.FILE,
            ),
        )
