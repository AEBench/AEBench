from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.utils import BaseCheck

from .consts import (
	PRESENCE_ALTERNATIVES,
	PRESENCE_BINARIES,
	REQUIRED_BINARIES,
	REQUIRED_PY_MODULES,
	TIME_BINARY_PATH,
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Verify the agent-built `koala` image contains the suite's toolchain."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = []

		# Tools with a clean --version: enforce a minimum.
		for binary, version in REQUIRED_BINARIES.items():
			checks.append(
				self.version_check(
					name=f"{binary}_version",
					cmd=(binary, "--version"),
					min_version=version,
				)
			)

		# Presence-only tools: `command -v` succeeds iff on PATH.
		for binary in PRESENCE_BINARIES:
			checks.append(
				self.command_check(
					name=f"{binary}_present",
					cmd=f"command -v {binary}",
					use_shell=True,
					timeout_seconds=30.0,
				)
			)

		# Tools with alternative executable names: pass if ANY resolves.
		for label, names in PRESENCE_ALTERNATIVES.items():
			alt = " || ".join(f"command -v {name}" for name in names)
			checks.append(
				self.command_check(
					name=f"{label}_present",
					cmd=alt,
					use_shell=True,
					timeout_seconds=30.0,
				)
			)

		# GNU time (noisy/non-zero --version on some builds) -> path check.
		checks.append(
			self.path_check(
				name="gnu_time_present",
				path=TIME_BINARY_PATH,
				kind=PathKind.FILE,
			)
		)

		# Python analysis / ml / weather dependencies import cleanly.
		modules = ", ".join(REQUIRED_PY_MODULES)
		checks.append(
			self.command_check(
				name="python_deps_importable",
				cmd=("python3", "-c", f"import {modules}"),
				timeout_seconds=120.0,
			)
		)

		return tuple(checks)
