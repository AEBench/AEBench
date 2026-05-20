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
				name="racket",
				cmd=("racket", "--version"),
				min_version=(8, 0, 0),
			),
			DependencyVersionCheck(
				name="rustc",
				cmd=("rustc", "--version"),
				min_version=(1, 60, 0),
				optional=True,
			),
			DependencyVersionCheck(
				name="make",
				cmd=("make", "--version"),
				min_version=(0, 0, 0),
				optional=True,
			),
			FilesystemPathCheck(
				name="repo_root_exists",
				path=repo_root,
				path_type=PathType.DIRECTORY,
			),
		)
