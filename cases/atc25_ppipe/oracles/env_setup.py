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

        reqs: list[utils.BaseCheck] = [
            DependencyVersionCheck(
                name="git",
                cmd=("git", "--version"),
                required_version=(2, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionCheck(
                name="git_lfs",
                cmd=("git-lfs", "--version"),
                required_version=(2, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionCheck(
                name="python",
                cmd=("python3", "--version"),
                required_version=(3, 12, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionCheck(
                name="gurobi",
                cmd=("python3", "-c", "import gurobipy; print(gurobipy.gurobi.version())"),
                required_version=(10, 0, 0),
                compare=VersionCompare.GEQ,
                optional=True,
            ),
            DependencyVersionCheck(
                name="java",
                cmd=("java", "-version"),
                required_version=(11, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            FilesystemPathCheck(
                name="repo_root_exists",
                path=repo_root,
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="requirements_txt_exists",
                path=repo_root / "requirements.txt",
                path_type=PathType.FILE,
            ),
            FilesystemPathCheck(
                name="cluster_sim_dir_exists",
                path=repo_root / "cluster-sim",
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="milp_solver_dir_exists",
                path=repo_root / "milp_solver",
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="data_dir_exists",
                path=repo_root / "data",
                path_type=PathType.DIRECTORY,
            ),
        ]
        return tuple(reqs)
