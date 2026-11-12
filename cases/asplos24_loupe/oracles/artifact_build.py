from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		helper = self.workspace_path("src", "seccomp-run")
		return (
			self.path_check(
				name="seccomp_runner_built",
				path=helper,
				kind=PathKind.FILE,
			),
			self.command_check(
				name="seccomp_runner_executable",
				cmd=("test", "-x", str(helper)),
				timeout_seconds=30.0,
			),
			self.command_check(
				name="loupe_base_image_built",
				cmd=("docker", "image", "inspect", "loupe-base:latest"),
				timeout_seconds=60.0,
			),
			self.command_check(
				name="compatible_static_analysis_packages",
				cmd=(
					"docker",
					"run",
					"--rm",
					"loupe-base:latest",
					"python3",
					"-c",
					"import capstone, lief; assert hasattr(lief, 'EXE_FORMATS'); assert capstone.__version__ == '4.0.2'",
				),
				timeout_seconds=60.0,
			),
		)
