from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import CommandCheck, PathCheck, PathKind
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .common import find_outputs_dir

_BUILD_MODE_ENV = "AE_CINNAMON_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(BaseCheck):
	mode: str

	def check(self, _executor: RuntimeCheckExecutor) -> CheckResult:
		return CheckResult.failure(
			f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
		)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	@staticmethod
	def _build_mode() -> str:
		raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
		return raw or "verify"

	def requirements(self) -> Sequence[BaseCheck]:
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
			# Running build_cinnamon.sh produces libcinnamon.so, which SST needs to 
			# simulate for the experiments; in turn, it will write out a set of PDFs
			# which we consider evidence of building CINNAMON
			outputs_dir = (
				find_outputs_dir(repo_root, executor=self.executor)
				or repo_root / "outputs"
			)
			return tuple(
				PathCheck(
					name=f"built_simulator_produced_{pdf_name.split('.')[0]}",
					path=outputs_dir / pdf_name,
					kind=PathKind.FILE,
				)
				for pdf_name in ("keyswitch_comparison.pdf", "bootstrap_comparison.pdf")
			)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)
