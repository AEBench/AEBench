from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
	CaseOracleEnvSetupBase,
	PathCheck,
	PathKind,
	VersionCheck,
)
from evaluator.oracles.utils import BaseCheck

from .consts import DOCKER_MIN_VERSION, DOCKERFILE_PATH, README_PATH, RUN_SH_PATH


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.artifact_path()

		return (
			VersionCheck(
				name="docker",
				cmd=("docker", "--version"),
				min_version=DOCKER_MIN_VERSION,
			),
			PathCheck(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
			PathCheck(
				name="dockerfile_exists",
				path=repo_root / DOCKERFILE_PATH,
				kind=PathKind.FILE,
			),
			PathCheck(
				name="readme_exists",
				path=repo_root / README_PATH,
				kind=PathKind.FILE,
                optional=True,
			),
			PathCheck(
				name="run_script_exists",
				path=repo_root / RUN_SH_PATH,
				kind=PathKind.FILE,
			),
		)
