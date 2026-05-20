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
		repo_root = self.paths.workspace_dir

		return (
			DependencyVersionCheck(
				name="docker",
				cmd=("docker", "--version"),
				min_version=(26, 0, 0),
			),
			FilesystemPathCheck(
				name="repo_root_exists",
				path=repo_root,
				path_type=PathType.DIRECTORY,
			),
			FilesystemPathCheck(
				name="dockerfile_exists",
				path=repo_root / "Dockerfile",
				path_type=PathType.FILE,
			),
			FilesystemPathCheck(
				name="run_script_exists",
				path=repo_root / "run.sh",
				path_type=PathType.FILE,
			),
		)
