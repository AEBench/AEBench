from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
	CaseOracleEnvSetupBase,
	PathKind,
)
from evaluator.oracles.reporting import BaseCheck

from .consts import DOCKER_MIN_VERSION, DOCKERFILE_PATH, README_PATH


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:

		return (
			self.version_check(
				name="docker",
				cmd=("docker", "--version"),
				min_version=DOCKER_MIN_VERSION,
				optional=True,
			),
			self.path_check(
				name="repo_root_exists",
				path=self.artifact_path(),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="dockerfile_exists",
				path=self.artifact_path(DOCKERFILE_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="readme_exists",
				path=self.artifact_path(README_PATH),
				kind=PathKind.FILE,
			),
		)
