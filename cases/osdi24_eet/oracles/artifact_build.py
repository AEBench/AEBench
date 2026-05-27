from __future__ import annotations

import os
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir
		cpu_count = os.cpu_count() or 1
		make_jobs = max(1, cpu_count // 2)

		return (
			BuildCommandCheck(
				name=f"eet_make_j{make_jobs}",
				cwd=repo_root,
				cmd=("make", f"-j{make_jobs}"),
				timeout_seconds=600.0,
			),
		)