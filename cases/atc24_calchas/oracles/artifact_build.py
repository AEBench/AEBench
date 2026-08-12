from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

from .parsing import PythonSourcesCheck

_ENTRYPOINTS = (
	"Experiments/Diff_model.py",
	"Experiments/Prediction_performance.py",
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Compile both pure-Python experiment entrypoints."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			PythonSourcesCheck(
				name="experiment_entrypoints_compile",
				root=self.runtime_path(),
				paths=_ENTRYPOINTS,
			),
		)
