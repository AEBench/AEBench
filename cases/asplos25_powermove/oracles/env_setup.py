from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

_PYTHON_PACKAGES = ("qiskit", "networkx", "numpy")


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 10, 0),
			),
			*(
				self.version_check(
					name=f"{package}_version",
					cmd=("python3", "-c", f"import {package}; print({package}.__version__)"),
					min_version=(1, 0, 0) if package == "qiskit" else None,
					version_regex=r"([0-9]+(?:\.[0-9]+){1,2})",
				)
				for package in _PYTHON_PACKAGES
			),
			self.path_check(
				name="test_scripts_dir",
				path=self.runtime_path("test_scripts"),
				kind=PathKind.DIRECTORY,
			),
		)
