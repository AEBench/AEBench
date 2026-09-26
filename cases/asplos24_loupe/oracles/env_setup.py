from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		python = self.workspace_path(".venv", "bin", "python")
		return (
			self.version_check(
				name="docker_version",
				cmd=("docker", "--version"),
				min_version=(20, 10, 0),
			),
			self.version_check(
				name="python_version",
				cmd=(str(python), "--version"),
				min_version=(3, 10, 0),
			),
			self.command_check(
				name="gitpython_importable",
				cmd=(str(python), "-c", "import git; print(git.__version__)"),
				timeout_seconds=30.0,
			),
			self.path_check(
				name="loupe_entrypoint",
				path=self.workspace_path("loupe"),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="explorer_entrypoint",
				path=self.workspace_path("explore.py"),
				kind=PathKind.FILE,
			),
		)
