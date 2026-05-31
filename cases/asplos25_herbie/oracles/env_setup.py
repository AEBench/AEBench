from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import (
        CommandCheck,
	VersionCheck,
	PathCheck,
	PathKind,
)

class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self._workspace_dir

		return (
			VersionCheck(
				name="racket",
				cmd=("racket", "--version"),
				min_version=(8, 0, 0),
			),
			VersionCheck(
				name="rustc",
				cmd=("rustc", "--version"),
				min_version=(1, 60, 0),
				optional=True,
			),
			VersionCheck(
				name="make",
				cmd=("make", "--version"),
				min_version=(0, 0, 0),
				optional=True,
			),
                        PathCheck(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
		)
