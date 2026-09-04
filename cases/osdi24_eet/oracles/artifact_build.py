from __future__ import annotations

import os
from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()
		cpu_count = os.cpu_count() or 1
		make_jobs = max(1, cpu_count // 2)

		return (
			self.command_check(
				name=f"eet_make_j{make_jobs}",
				cwd=repo_root,
				cmd=("make", f"-j{make_jobs}"),
				timeout_seconds=600.0,
			),
		)
