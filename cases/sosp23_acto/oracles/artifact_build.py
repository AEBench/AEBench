from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind, utils


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		workspace = self.workspace_path()

		return (
			self.command_check(
				name="acto_make",
				cwd=workspace,
				cmd=("make",),
				timeout_seconds=300.0,
			),
			self.path_check(
				name="k8sutil_shared_library",
				path=workspace / "acto" / "k8s_util" / "lib" / "k8sutil.so",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="ssa_shared_library",
				path=workspace / "ssa" / "libanalysis.so",
				kind=PathKind.FILE,
			),
		)
