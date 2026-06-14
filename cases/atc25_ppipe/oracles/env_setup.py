from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import (
	VersionCheck,
	PathCheck,
	PathKind,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.workspace_path()

		return (
			VersionCheck(
				name="git",
				cmd=("git", "--version"),
				min_version=(2, 0, 0),
			),
			VersionCheck(
				name="git_lfs",
				cmd=("git-lfs", "--version"),
				min_version=(2, 0, 0),
				version_regex=r"git-lfs/(\d+\.\d+\.\d+)",
			),
			VersionCheck(
				name="python",
				cmd=("python3", "--version"),
				min_version=(3, 11, 0),
			),
			#VersionCheck(
				#name="gurobi",
				#cmd=(
					#"python3",
					#"-c",
					#"import gurobipy; print('.'.join(map(str, gurobipy.gurobi.version())))",
				#),
				#min_version=(10, 0, 0),
				#optional=True,
			#),
			VersionCheck(
                name="conda",
                cmd=("conda", "--version"),
                min_version=(4, 9, 0),
            ),
			#VersionCheck(
                #name="java",
				#cmd=("java", "-version"),
                #cmd=(
                    #"bash", 
                    #"-c", 
                    #"java -version 2>&1 | perl -pe 's/\"(\\d+)\"/ $1.0.0 /'" #address changes in case.toml
                #),
				
                #min_version=(11, 0, 0),
				#max_version=(21,0,0),
            #),
			PathCheck(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="requirements_txt_exists",
				path=repo_root / "requirements.txt",
				kind=PathKind.FILE,
			),
			PathCheck(
				name="cluster_sim_dir_exists",
				path=repo_root / "cluster-sim",
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="milp_solver_dir_exists",
				path=repo_root / "milp_solver",
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="data_dir_exists",
				path=repo_root / "data",
				kind=PathKind.DIRECTORY,
			),
		)