from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

_PYTHON_MIN_VERSION = (3, 7, 0)
_REQUIRED_PYTHON_MODULES = ("matplotlib", "numpy", "pandas", "scipy", "seaborn")
_README_PATH = "README.md"


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=_PYTHON_MIN_VERSION,
			),
			self.path_check(
				name="artifact_readme",
				path=self.runtime_path(_README_PATH),
				kind=PathKind.FILE,
			),
		]
		for module in _REQUIRED_PYTHON_MODULES:
			checks.append(
				self.command_check(
					name=f"python_module_{module}",
					cmd=("python3", "-B", "-c", f"import {module}"),
					timeout_seconds=120.0,
				)
			)
		return tuple(checks)
