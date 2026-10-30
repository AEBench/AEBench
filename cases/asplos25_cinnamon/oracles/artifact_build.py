from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import CommandCheck, PathCheck, PathKind
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .common import PdfCreatedDuringRunCheck, find_outputs_dir

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
			# Without a built libcinnamon.so every simulation aborts and plot.py
			# writes nothing, so the comparison PDFs prove the build works.
			outputs_dir = (
				find_outputs_dir(repo_root, executor=self.executor)
				or repo_root / "outputs"
			)
			runtime_result = self.context.runtime_result
			checks: list[BaseCheck] = []
			for pdf_name in ("keyswitch_comparison.pdf", "bootstrap_comparison.pdf"):
				checks.append(
					PathCheck(
						name=f"built_simulator_produced_{pdf_name.split('.')[0]}",
						path=outputs_dir / pdf_name,
						kind=PathKind.FILE,
					)
				)
				checks.append(
					PdfCreatedDuringRunCheck(
						name=f"created_during_run_{pdf_name.split('.')[0]}",
						pdf_path=outputs_dir / pdf_name,
						started_at=None if runtime_result is None else runtime_result.started_at,
						finished_at=None if runtime_result is None else runtime_result.finished_at,
					)
				)
			return tuple(checks)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)
