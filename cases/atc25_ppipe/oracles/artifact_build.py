from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType


_BUILD_MODE_ENV = "AE_PPIPE_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0

_SIMULATOR_BINARY = "cluster-sim/build/install/cluster-sim/bin/cluster-sim"


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):
	mode: str

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		return utils.CheckResult.failure(
			f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
		)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	@staticmethod
	def _build_mode() -> str:
		raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
		return raw or "verify"

	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir

		mode = self._build_mode()

		if mode == "command":
			return (
				BuildCommandCheck(
					name="gradle_install_dist",
					cwd=repo_root / "cluster-sim",
					cmd=("./gradlew", "installDist"),
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
			)

		if mode == "verify":
			return (
				FilesystemPathCheck(
					name="simulator_binary",
					path=repo_root / _SIMULATOR_BINARY,
					path_type=PathType.FILE,
				),
			)

		return (InvalidBuildModeCheck(name="build_mode_valid", mode=mode),)
