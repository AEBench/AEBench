from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase

_BUILD_COMMAND: tuple[str, ...] = ("make", "install")
_BUILD_TIMEOUT_SECONDS = 3600.0
_BUILD_MODE_ENV = "AE_HERBIE_BUILD_MODE"


@dataclass(frozen=True, slots=True, kw_only=True)
class HerbieBinaryLocatedCheck(utils.BaseCheck):
	"""Fail if herbie binary or Racket entry point is unavailable."""

	repo_root: "os.PathLike[str]"
	executor: utils.RuntimeCheckExecutor | None = None

	def check(self, *_args: object, **_kwargs: object) -> utils.CheckResult:
		from pathlib import Path

		resolved = utils.resolve_check_executable("herbie", executor=self.executor)
		if resolved is not None:
			return utils.CheckResult.success()

		rkt_paths = (
			Path(self.repo_root) / "src" / "herbie.rkt",
			# arith25 tag uses src/main.rkt instead of src/herbie.rkt.
			Path(self.repo_root) / "src" / "main.rkt",
		)
		if any(rkt_path.is_file() for rkt_path in rkt_paths):
			return utils.CheckResult.success()

		home = Path.home()
		for candidate in home.glob(".racket/*/bin/herbie"):
			if candidate.is_file():
				return utils.CheckResult.success()

		return utils.CheckResult.failure(
			"herbie binary not found on PATH, in ~/.racket/*/bin/, "
			"and neither src/herbie.rkt nor src/main.rkt found in repo"
		)


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
