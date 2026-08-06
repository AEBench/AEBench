from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

_IMPORT_SCOPED = """
import sys

sys.path.insert(0, "Experiments")
import Diff_model
import Prediction_performance

print("Figure 12/13 entrypoints import successfully")
"""


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Calchas is pure Python; load both scoped experiment entrypoints."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="scoped_entrypoints_import",
				cmd=("python3", "-B", "-c", _IMPORT_SCOPED),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
				signature="Figure 12/13 entrypoints import successfully",
			),
		)
