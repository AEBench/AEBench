from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	PYTHON_MIN_VERSION,
	README_PATH,
	REQUIRED_PY_MODULES,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm the environment is provisioned: a usable python3, the fetched repo
	and its dependency manifest, and the third-party deps installed & importable.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=PYTHON_MIN_VERSION,
			),
			self.path_check(
				name="readme_exists",
				path=self.runtime_path(README_PATH),
				kind=PathKind.FILE,
			),
		]

		for module in REQUIRED_PY_MODULES:
			checks.append(
				self.command_check(
					name=f"dep_{module}_importable",
					cmd=("python3", "-c", f"import {module}"),
					timeout_seconds=120.0,
				)
			)

		return tuple(checks)
