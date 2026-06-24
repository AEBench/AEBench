from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.utils import BaseCheck

from .consts import (
	REQUIRED_BINARIES,
	REQUIRED_PY_MODULES,
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Verify the agent-built `crocus` image contains the experiment toolchain."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = []

		# Executables the experiments rely on are on PATH inside the image.
		for binary, version in REQUIRED_BINARIES.items():
			checks.append(
				self.version_check(
					name=f"{binary}_version",
					cmd=(binary, "--version"),
					min_version=version,
				)
			)

		# Python analysis-script dependencies import cleanly.
		modules = ", ".join(REQUIRED_PY_MODULES)
		checks.append(
			self.command_check(
				name="python_deps_importable",
				cmd=("python3", "-c", f"import {modules}"),
				timeout_seconds=60.0,
			)
		)

		return tuple(checks)
