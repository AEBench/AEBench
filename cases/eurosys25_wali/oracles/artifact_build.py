from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="wali_runtime_executes_wasm",
				cmd=(
					"./iwasm",
					"-v=0",
					"--stack-size=524288",
					"sample-apps/sqlite/sqlite3.wasm",
					"-version",
				),
				timeout_seconds=30.0,
				signature="3.45.0",
			),
		)
