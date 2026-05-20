from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.case_base import CaseOracleEnvSetupBase
from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	FilesystemPathCheck,
	PathType,
	VersionCompare,
)

from evaluator.oracles import utils


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir

		return (
			DependencyVersionCheck(
				name="python3",
				cmd=("python3", "--version"),
				required_version=(3, 10, 0),
				compare=VersionCompare.GEQ,
			),
			DependencyVersionCheck(
				name="pip",
				cmd=("python3", "-m", "pip", "--version"),
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
