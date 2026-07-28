from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	CAPACITY_LATENCY_FILE,
	CLAIM_TOL,
	DEFAULT_REL_TOL,
	ONE_AND_INF_FILE,
	ONE_AND_INF_INF_ROW,
	ONE_AND_INF_ONE_ROW,
	SCOPED_OUTPUTS,
)
from .parsing import (
	CapacityMonotonicCheck,
	CsvNumericMatchCheck,
	OneVsInfSavingsCheck,
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the agent-produced data_output CSVs for the scoped experiments
	against the committed references, plus two hardware-independent paper claims.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		executor = self.executor
		checks: list[BaseCheck] = []

		# (A+B) Per-experiment numeric reproduction vs committed reference CSVs.
		for exp_dir, filename, ref_key in SCOPED_OUTPUTS:
			label = f"{ref_key}/{filename}"
			checks.append(
				CsvNumericMatchCheck(
					name=f"csv_{ref_key}_{filename.removesuffix('.csv')}",
					label=label,
					observed_path=self.runtime_path(exp_dir, "data_output", filename),
					reference_path=self.ref_path(ref_key, filename),
					rel_tol=DEFAULT_REL_TOL,
					executor=executor,
				)
			)

		# (C) Paper claim: unlimited migration saves >= single migration per region.
		checks.append(
			OneVsInfSavingsCheck(
				name="spatial_inf_ge_one_savings",
				observed_path=self.runtime_path(ONE_AND_INF_FILE),
				one_row=ONE_AND_INF_ONE_ROW,
				inf_row=ONE_AND_INF_INF_ROW,
				tol=CLAIM_TOL,
				executor=executor,
			)
		)

		# (D) Paper claim: more idle capacity -> non-increasing emissions per latency.
		checks.append(
			CapacityMonotonicCheck(
				name="spatial_capacity_reduces_emissions",
				observed_path=self.runtime_path(CAPACITY_LATENCY_FILE),
				tol=CLAIM_TOL,
				executor=executor,
			)
		)

		return tuple(checks)
