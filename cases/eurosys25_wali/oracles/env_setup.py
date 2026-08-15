from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.path_check(
				name="wali_source_tree",
				path=self.runtime_path("README.md"),
				kind=PathKind.FILE,
			),
			self.version_check(
				name="python3",
				cmd=("python3", "--version"),
				min_version=(3, 6, 0),
			),
			self.version_check(
				name="cmake",
				cmd=("cmake", "--version"),
				min_version=(3, 14, 0),
			),
			self.version_check(
				name="bash",
				cmd=("bash", "--version"),
				min_version=(4, 0, 0),
			),
			self.command_check(
				name="make_available",
				cmd="command -v make",
				use_shell=True,
				timeout_seconds=10.0,
			),
			self.command_check(
				name="ninja_available",
				cmd="command -v ninja",
				use_shell=True,
				timeout_seconds=10.0,
			),
			self.command_check(
				name="c_compiler_available",
				cmd="command -v cc",
				use_shell=True,
				timeout_seconds=10.0,
			),
			self.command_check(
				name="cxx_compiler_available",
				cmd="command -v c++",
				use_shell=True,
				timeout_seconds=10.0,
			),
			self.command_check(
				name="python_analysis_stack",
				cmd=(
					"python3",
					"-c",
					"import matplotlib, numpy, pandas, scipy, tqdm",
				),
				timeout_seconds=30.0,
			),
			self.command_check(
				name="docker_daemon",
				cmd=("docker", "version", "--format", "{{.Server.Version}}"),
				timeout_seconds=30.0,
			),
		)
