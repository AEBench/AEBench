from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

from .consts import RUN_PY_PATH


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""GAIA has no compiled artifact and no built image, so "build" means the
	artifact's own code is in a runnable state: the entrypoint loads its full
	sibling-module import graph (carbon, task, scheduling, cluster) and its CLI
	builds. `python3 src/run.py -h` exercises exactly that. (Third-party deps are
	an environment concern, verified in env_setup.)
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="entrypoint_imports",
				cmd=("python3", RUN_PY_PATH, "-h"),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
			),
		)
