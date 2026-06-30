from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, utils


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		return (
			self.command_check(
				name="acto_make_lib",
				cwd=self.workspace_path("acto"),
				cmd=("make", "lib"),
				timeout_seconds=60.0,
			),
		)
