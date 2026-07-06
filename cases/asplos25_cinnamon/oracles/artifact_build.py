from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathCheck, PathKind, CommandCheck


_BUILD_MODE_ENV = "AE_CINNAMON_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0



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

		mode = self._build_mode()

		if mode == "command":
			return (
				CommandCheck(
					name="build_sst_simulator_in_docker",
					cwd=repo_root,
					cmd=("docker", "exec", "-it", "cinnamon", "./build_cinnamon.sh"),
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
				
			)

		if mode == "verify":
			return (
				CommandCheck(
                    name="cinnamon_container_is_running",
                    cmd=("docker", "inspect", "-f", "{{.State.Running}}", "cinnamon"),
                    signature="true",
                    timeout_seconds=10.0,
                ),
				CommandCheck(
                    name="build_script_exists",
                    cmd=("docker", "exec", "cinnamon", "ls", "build_cinnamon.sh"),
                    timeout_seconds=10.0,
                ),
				CommandCheck(
                    name="run_keyswitch_comparison_script_exists",
                    cmd=("docker", "exec", "cinnamon", "ls", "run_keyswitch_comparison.sh"),
                    timeout_seconds=10.0,
                ),
                CommandCheck(
                    name="run_bootstrap_comparison_script_exists",
                    cmd=("docker", "exec", "cinnamon", "ls", "run_bootstrap_comparison.sh"),
                    timeout_seconds=10.0,
                ),
                CommandCheck(
                    name="run_performance_script_exists",
                    cmd=("docker", "exec", "cinnamon", "ls", "run_performance.sh"),
                    timeout_seconds=10.0,
                )
                
			)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)