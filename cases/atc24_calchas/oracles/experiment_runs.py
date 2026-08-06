from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .parsing import CalchasMetricsCheck, ExperimentLogCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate complete Figure 12/13 logs and their prediction metrics."""

	def requirements(self) -> Sequence[BaseCheck]:
		reference = json.loads(self.ref_path("results.ref.json").read_text(encoding="utf-8"))
		return (
			ExperimentLogCheck(
				name="figure12_log",
				path=self.runtime_path("evaluation", "figure12.log"),
				figure="figure12",
				required=tuple(reference["log_signatures"]["figure12"]),
				executor=self.executor,
			),
			ExperimentLogCheck(
				name="figure13_log",
				path=self.runtime_path("evaluation", "figure13.log"),
				figure="figure13",
				required=tuple(reference["log_signatures"]["figure13"]),
				executor=self.executor,
			),
			CalchasMetricsCheck(
				name="prediction_metrics",
				root=self.runtime_path("evaluation"),
				reference=reference,
				executor=self.executor,
			),
		)
