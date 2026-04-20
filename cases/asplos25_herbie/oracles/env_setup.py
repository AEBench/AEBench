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
                name="racket",
                cmd=("racket", "--version"),
                required_version=(8, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionCheck(
                name="rustc",
                cmd=("rustc", "--version"),
                required_version=(1, 60, 0),
                compare=VersionCompare.GEQ,
                optional=True,
            ),
            DependencyVersionCheck(
                name="make",
                cmd=("make", "--version"),
                required_version=(0, 0, 0),
                compare=VersionCompare.GEQ,
                optional=True,
            ),
            FilesystemPathCheck(
                name="repo_root_exists",
                path=repo_root,
                path_type=PathType.DIRECTORY,
            ),
        )
