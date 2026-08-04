from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
    CaseOracleEnvSetupBase,
    PathCheck,
    PathKind,
    VersionCheck,
)
from evaluator.oracles.reporting import BaseCheck

class OracleEnvSetup(CaseOracleEnvSetupBase):
    """Verify host environment tools (Conda) and repository root exist."""

    def requirements(self) -> Sequence[BaseCheck]:
        return (
            VersionCheck(
                name="conda",
                cmd=("conda", "--version"),
                min_version=(22, 9, 0), 
            ),
            PathCheck(
                name="repo_root_exists",
                path=self.artifact_path(),
                kind=PathKind.DIRECTORY,
            ),
            PathCheck(
                name="requirements",
                path=self.artifact_path("requirements.txt"),
                kind=PathKind.FILE,
            ),
            PathCheck(
                name="requirements_no_version_exists",
                path=self.artifact_path("requirementswithnoversion.txt"),
                kind=PathKind.FILE,
            ),
        )
