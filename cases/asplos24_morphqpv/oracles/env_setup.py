from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
    CaseOracleEnvSetupBase,
    PathKind,
)
from evaluator.oracles.reporting import BaseCheck

class OracleEnvSetup(CaseOracleEnvSetupBase):
    """Verify host environment tools (Conda) and repository root exist."""

    def requirements(self) -> Sequence[BaseCheck]:
        return (
            self.version_check(
                name="conda",
                cmd=("conda", "--version"),
                min_version=(22, 9, 0), 
            ),
            self.path_check(
                name="repo_root_exists",
                path=self.workspace_path(),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="requirements",
                path=self.workspace_path("requirements.txt"),
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="requirements_no_version_exists",
                path=self.workspace_path("requirementswithnoversion.txt"),
                kind=PathKind.FILE,
            ),
        )
