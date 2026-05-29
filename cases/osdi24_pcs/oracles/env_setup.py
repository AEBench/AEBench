from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleEnvSetupBase
from evaluator.oracles.env_setup_checks import (
    DependencyVersionCheck,
    FilesystemPathCheck,
    PathType,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            DependencyVersionCheck(
                name="python3_version",
                cmd=("python3", "--version"),
                min_version=(3, 10, 0),
            ),
            DependencyVersionCheck(
                name="pip_available",
                cmd=("python3", "-m", "pip", "--version"),
                min_version=(0, 0, 0),
                optional=True,
            ),
            FilesystemPathCheck(
                name="repo_root_exists",
                path=self.paths.workspace_dir,
                path_type=PathType.DIRECTORY,
            ),
        )