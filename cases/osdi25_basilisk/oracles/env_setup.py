from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import DAFNY_DIR

_PYTHON_MIN_VERSION = (3, 6, 0)
_DOTNET_MIN_VERSION = (6, 0, 0)
_JAVA_MIN_VERSION = (17, 0, 0)
_README_PATH = "README.md"


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm the documented Basilisk toolchain and source tree are present."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=_PYTHON_MIN_VERSION,
			),
			self.version_check(
				name="dotnet_version",
				cmd=("dotnet", "--version"),
				min_version=_DOTNET_MIN_VERSION,
			),
			self.version_check(
				name="java_version",
				cmd=("java", "-version"),
				min_version=_JAVA_MIN_VERSION,
			),
			self.version_check(
				name="javac_version",
				cmd=("javac", "-version"),
				min_version=_JAVA_MIN_VERSION,
			),
			self.command_check(
				name="make_available",
				cmd=("sh", "-lc", "command -v make"),
				timeout_seconds=30.0,
			),
			self.path_check(
				name="readme_exists",
				path=self.runtime_path(_README_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="local_dafny_source",
				path=self.runtime_path(DAFNY_DIR),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="basilisk_protocols_source",
				path=self.runtime_path("basilisk"),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="kondo_comparison_source",
				path=self.runtime_path("kondo"),
				kind=PathKind.DIRECTORY,
			),
		)
