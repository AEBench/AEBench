from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 8, 0),
			),
			self.version_check(
				name="cxx_compiler",
				cmd=("c++", "--version"),
				min_version=(7, 0, 0),
			),
			self.path_check(
				name="artifact_readme",
				path=self.runtime_path("README.rst"),
				kind=PathKind.FILE,
			),
		)
