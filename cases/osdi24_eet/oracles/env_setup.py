from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()

		return (
			self.version_check(
				name="docker",
				cmd=("docker", "--version"),
				min_version=(24, 0, 0),
			),
			self.version_check(
				name="gpp",
				cmd=("g++", "--version"),
				min_version=(13, 2, 0),
			),
			self.version_check(
				name="make",
				cmd=("make", "--version"),
				min_version=(4, 3, 0),
			),
			self.version_check(
				name="autoconf",
				cmd=("autoconf", "--version"),
				min_version=(2, 71, 0),
			),
			self.path_check(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="scripts_dir_exists",
				path=repo_root / "scripts",
				kind=PathKind.DIRECTORY,
			),
		)
