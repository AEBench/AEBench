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
                name="dotnet",
                cmd=("dotnet", "--version"),
                min_version=(6, 0, 0),
            ),
            DependencyVersionCheck(
                name="python3",
                cmd=("python3", "--version"),
                min_version=(3, 0, 0),
            ),
            FilesystemPathCheck(
                name="repo_root_exists",
                path=self.workspace_path(),
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="local_dafny_dir",
                path=self.workspace_path("local-dafny"),
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="kondo_prototypes_dir",
                path=self.workspace_path("kondoPrototypes"),
                path_type=PathType.DIRECTORY,
            ),
        )