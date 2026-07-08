from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
	CaseOracleEnvSetupBase,
	PathKind,
)
from evaluator.oracles.reporting import BaseCheck

from .consts import DOCKER_MIN_VERSION, DOCKERFILE_PATH, README_PATH, RUN_SH_PATH


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.artifact_path()

		return (
			self.version_check(
				name="docker",
				cmd=("docker", "--version"),
				min_version=DOCKER_MIN_VERSION,
				optional=True,
			),
			self.path_check(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="dockerfile_exists",
				path=repo_root / DOCKERFILE_PATH,
				kind=PathKind.FILE,
			),
			self.path_check(
				name="readme_exists",
				path=repo_root / README_PATH,
				kind=PathKind.FILE,
				optional=True,
			),
			self.path_check(
				name="run_script_exists",
				path=repo_root / RUN_SH_PATH,
				kind=PathKind.FILE,
			),
		)
