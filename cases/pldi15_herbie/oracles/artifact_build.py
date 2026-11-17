from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import CommandCheck
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

_BUILD_COMMAND: tuple[str, ...] = ("make", "install")
_BUILD_TIMEOUT_SECONDS = 3600.0
_BUILD_MODE_ENV = "AE_HERBIE_BUILD_MODE"


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
		repo_root = self._workspace_dir

		mode = self._build_mode()

		if mode == "command":
			return (
				CommandCheck(
					name="herbie_make_install",
					cwd=repo_root,
					cmd=_BUILD_COMMAND,
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
			)

		if mode == "verify":
			return (
				CommandCheck(
					name="herbie_cli_runs",
					cwd=repo_root,
					cmd=("racket", "src/main.rkt", "--help"),
					timeout_seconds=60.0,
				),
			)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)
