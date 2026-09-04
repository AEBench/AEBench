from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm the documented Kondo toolchain and source tree are present."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.version_check(
				name="dotnet",
				cmd=("dotnet", "--version"),
				min_version=(6, 0, 0),
			),
			self.version_check(
				name="java",
				cmd=("java", "-version"),
				min_version=(17, 0, 0),
			),
			self.version_check(
				name="javac",
				cmd=("javac", "-version"),
				min_version=(17, 0, 0),
			),
			self.version_check(
				name="python3",
				cmd=("python3", "--version"),
				min_version=(3, 6, 0),
			),
			self.command_check(
				name="make_available",
				cmd="command -v make",
				use_shell=True,
				timeout_seconds=10.0,
			),
			self.path_check(
				name="local_dafny_dir",
				path=self.runtime_path("local-dafny"),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="kondo_prototypes_dir",
				path=self.runtime_path("kondoPrototypes"),
				kind=PathKind.DIRECTORY,
			),
		)
