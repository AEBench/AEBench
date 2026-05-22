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
		repo_root = self.workspace_path()

		return (
			DependencyVersionCheck(
				name="git",
				cmd=("git", "--version"),
				min_version=(2, 0, 0),
			),
			DependencyVersionCheck(
				name="git_lfs",
				cmd=("git-lfs", "--version"),
				min_version=(2, 0, 0),
				version_regex=r"git-lfs/(\d+\.\d+\.\d+)",
			),
			DependencyVersionCheck(
				name="python",
				cmd=("python3", "--version"),
				min_version=(3, 12, 0),
			),
			DependencyVersionCheck(
				name="gurobi",
				cmd=(
					"python3",
					"-c",
					"import gurobipy; print('.'.join(map(str, gurobipy.gurobi.version())))",
				),
				min_version=(10, 0, 0),
				optional=True,
			),
			DependencyVersionCheck(
				name="java",
				cmd=("java", "-version"),
				min_version=(11, 0, 0),
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
		)