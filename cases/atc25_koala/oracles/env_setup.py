from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
	CaseOracleEnvSetupBase,
	PathCheck,
	PathKind,
	VersionCheck,
)
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	DOCKER_MIN_VERSION,
	DOCKERFILE_PATH,
	MAIN_SH_PATH,
	README_PATH,
	SETUP_SH_PATH,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Host side: Docker is new enough and the agent cloned a runnable Koala repo."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			VersionCheck(
				name="docker",
				cmd=("docker", "--version"),
				min_version=DOCKER_MIN_VERSION,
			),
			PathCheck(
				name="repo_root_exists",
				path=self.artifact_path(),
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="dockerfile_exists",
				path=self.artifact_path(DOCKERFILE_PATH),
				kind=PathKind.FILE,
			),
			PathCheck(
				name="readme_exists",
				path=self.artifact_path(README_PATH),
				kind=PathKind.FILE,
			),
			PathCheck(
				name="main_sh_exists",
				path=self.artifact_path(MAIN_SH_PATH),
				kind=PathKind.FILE,
			),
			PathCheck(
				name="setup_sh_exists",
				path=self.artifact_path(SETUP_SH_PATH),
				kind=PathKind.FILE,
			),
		)
