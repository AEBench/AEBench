from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.oracle_checks_runtime import (
	OraclePath,
	RuntimeCheckExecutor,
	RuntimePath,
	resolve_check_executable,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_BUILD_COMMAND: tuple[str, ...] = ("make", "install")
_BUILD_TIMEOUT_SECONDS = 3600.0
_BUILD_MODE_ENV = "AE_HERBIE_BUILD_MODE"


@dataclass(frozen=True, slots=True, kw_only=True)
class HerbieBinaryLocatedCheck(BaseCheck):
	"""Fail if herbie binary or Racket entry point is unavailable."""

	repo_root: OraclePath

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		from pathlib import Path

		resolved = resolve_check_executable("herbie", executor=executor)
		if resolved is not None:
			return CheckResult.success()

		rkt_paths = (
			Path(self.repo_root) / "src" / "herbie.rkt",
			# arith25 tag uses src/main.rkt instead of src/herbie.rkt.
			Path(self.repo_root) / "src" / "main.rkt",
		)
		if any(executor.path_is_file(rkt_path) for rkt_path in rkt_paths):
			return CheckResult.success()

		home = executor.read_env_var("HOME")
		if home and executor.glob(RuntimePath.from_parts(home), ".racket/*/bin/herbie"):
			return CheckResult.success()

		return CheckResult.failure(
			"herbie binary not found on PATH, in ~/.racket/*/bin/, "
			"and neither src/herbie.rkt nor src/main.rkt found in repo"
		)


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
				self.command_check(
					name="herbie_make_install",
					cwd=repo_root,
					cmd=_BUILD_COMMAND,
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
			)

		if mode == "verify":
			return (
				HerbieBinaryLocatedCheck(
					name="herbie_binary_located",
					repo_root=repo_root,
				),
			)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)
