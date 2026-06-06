from __future__ import annotations

import sys
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir
		python_executable = sys.executable or "python"

		return (
			BuildCommandCheck(
				name="neutrino_import_test",
				cwd=repo_root,
				cmd=(python_executable, "-c", "import neutrino; print(neutrino.__file__)"),
				timeout_seconds=30.0,
			),
			BuildCommandCheck(
				name="neutrino_cli_help",
				optional=True,
				cwd=repo_root,
				cmd=("neutrino", "--help"),
				timeout_seconds=30.0,
			),
		)