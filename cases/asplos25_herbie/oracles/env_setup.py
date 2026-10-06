from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()

		return (
			self.version_check(
				name="racket",
				cmd=("racket", "--version"),
				min_version=(8, 0, 0),
			),
			self.version_check(
				name="rustc",
				cmd=("rustc", "--version"),
				min_version=(1, 60, 0),
				optional=True,
			),
			self.version_check(
				name="make",
				cmd=("make", "--version"),
				min_version=(4, 4, 0),
				version_regex=r"GNU Make\s+([0-9]+(?:\.[0-9]+){1,2})",
			),
			self.path_check(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
		)
