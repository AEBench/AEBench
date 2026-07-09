from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            self.version_check(
                name="python3_version",
                cmd=("python3", "--version"),
                min_version=(3, 10, 0),
            ),
            self.version_check(
                name="pip_available",
                cmd=("python3", "-m", "pip", "--version"),
                min_version=(0, 0, 0),
                optional=True,
            ),
            self.path_check(
                name="repo_root_exists",
                path=self.workspace_path(),
                kind=PathKind.DIRECTORY,
            ),
        )
