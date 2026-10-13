from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.path_check(
				name="artifact_readme",
				path=self.runtime_path("README.md"),
				kind=PathKind.FILE,
			),
			self.command_check(
				name="linux_x86_64_host",
				cmd=("uname", "-sm"),
				signature="Linux x86_64",
				timeout_seconds=10.0,
			),
			self.version_check(
				name="gcc",
				cmd=("gcc", "--version"),
				min_version=(12, 0, 0),
			),
			self.version_check(
				name="clang",
				cmd=("clang", "--version"),
				min_version=(16, 0, 0),
			),
			self.version_check(
				name="cmake",
				cmd=("cmake", "--version"),
				min_version=(3, 15, 0),
			),
			self.version_check(
				name="python3",
				cmd=("python3", "--version"),
				min_version=(3, 7, 0),
			),
		)
