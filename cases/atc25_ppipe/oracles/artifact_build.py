from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.checks import CommandCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathCheck, PathKind


_BUILD_MODE_ENV = "AE_PPIPE_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0

_SIMULATOR_BINARY = "cluster-sim/build/install/cluster-sim/bin/cluster-sim"


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):
	mode: str

	def check(self) -> utils.CheckResult:
		return utils.CheckResult.failure(
			f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
		)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	@staticmethod
	def _build_mode() -> str:
		raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
		return raw or "verify"

	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.workspace_path()
		simulator_binary = repo_root / _SIMULATOR_BINARY

		mode = self._build_mode()

		if mode == "command":
			return (
				CommandCheck(
					name="gradle_install_dist",
					cwd=repo_root,
					cmd=(
						"bash",
						"-c", 
						#covers conda build, python version check, gurobi, and change into java simulator folder
						"conda create -n ppipe python=3.12 -y && conda run -n ppipe pip install -r requirements.txt && cd cluster-sim && ./gradlew installDist",
					),
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
				PathCheck(
					name="simulator_binary",
					path=simulator_binary,
					kind=PathKind.FILE,
				),
			)

		if mode == "verify":
			return (
				PathCheck(
					name="simulator_binary",
					path=simulator_binary,
					kind=PathKind.FILE,
				),
			)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)