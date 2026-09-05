from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Verify the documented interpreter and Python dependencies."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 6, 0),
			),
			self.path_check(
				name="readme_exists",
				path=self.runtime_path("README.md"),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="requirements_exists",
				path=self.runtime_path("requirements.txt"),
				kind=PathKind.FILE,
			),
		]
		for module in ("numpy", "pandas", "sklearn", "matplotlib"):
			checks.append(
				self.command_check(
					name=f"dep_{module}_importable",
					cmd=("python3", "-B", "-c", f"import {module}"),
					timeout_seconds=120.0,
				)
			)
		return tuple(checks)
