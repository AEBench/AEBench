from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="powermove_compiler_smoke",
				cmd=("python3", "run_evaluation.py", "--smoke"),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
			),
		)
