from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleEnvSetupBase
from evaluator.oracles.env_setup_checks import (
    DependencyVersionCheck,
    FilesystemPathCheck,
    PathType,
    VersionCompare,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):

    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.paths.workspace_dir

        return (
            DependencyVersionCheck(
                name="dotnet",
                cmd=("dotnet", "--version"),
                required_version=(6, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionCheck(
                name="python3",
                cmd=("python3", "--version"),
                required_version=(3, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            FilesystemPathCheck(
                name="repo_root_exists",
                path=repo_root,
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="local_dafny_dir",
                path=repo_root / "local-dafny",
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="kondo_prototypes_dir",
                path=repo_root / "kondoPrototypes",
                path_type=PathType.DIRECTORY,
            ),
        )
