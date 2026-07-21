from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	CARBON_TRACE_PATH,
	FIGURE_SCRIPTS,
	TASK_TRACE_PATH,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Verify the experiment inputs are staged: the task/carbon traces and the
	figure driver scripts. The artifact entrypoint (src/run.py) is the artifact's
	own code, validated in artifact_build; the figure scripts are the drivers the
	agent invokes (they call run.py internally).
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.path_check(
				name="task_trace_pai_1k",
				path=self.runtime_path(TASK_TRACE_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="carbon_trace_au_sa",
				path=self.runtime_path(CARBON_TRACE_PATH),
				kind=PathKind.FILE,
			),
		]
		for rel in FIGURE_SCRIPTS:
			name = rel.rsplit("/", 1)[-1].replace(".", "_").replace("-", "_")
			checks.append(
				self.path_check(
					name=f"script_{name}",
					path=self.runtime_path(rel),
					kind=PathKind.FILE,
				)
			)
		return tuple(checks)
