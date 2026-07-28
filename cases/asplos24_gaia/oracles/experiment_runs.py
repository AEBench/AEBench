from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	DEFAULT_REL_TOL,
	FIG11_RESERVED_BASELINE,
	FIG11_RESERVED_STEPS,
	FIG89_BASELINE,
	FIG89_CARBON_AWARE,
	RESULTS_REF,
	RESULTS_SUBDIR,
)
from .parsing import CarbonReductionCheck, GaiaSummaryCheck, ReservedCostReductionCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the agent-produced results/simulation/pai_1k/*.csv against the
	committed ground truth and the paper's carbon/cost trends.
	"""

	def _summary_path(self, filename: str):
		return self.runtime_path(RESULTS_SUBDIR, filename)

	def requirements(self) -> Sequence[BaseCheck]:
		ref = json.loads(self.ref_path(RESULTS_REF).read_text(encoding="utf-8"))
		runs = ref["runs"]
		rel_tol = float(ref.get("rel_tolerance", DEFAULT_REL_TOL))
		executor = self.executor

		checks: list[BaseCheck] = []

		# the results directory exists.
		checks.append(
			self.path_check(
				name="results_dir_exists",
				path=self.runtime_path(RESULTS_SUBDIR),
				kind=PathKind.DIRECTORY,
			)
		)

		# Per-run: file present, well-formed, and numerically reproduces
		# the reference carbon_cost / dollar_cost within relative tolerance.
		for filename in sorted(runs):
			expected = runs[filename]
			checks.append(
				GaiaSummaryCheck(
					name=f"summary_{filename.removesuffix('.csv')}",
					path=self._summary_path(filename),
					filename=filename,
					expected_carbon=float(expected["carbon_cost"]),
					expected_dollar=float(expected["dollar_cost"]),
					rel_tol=rel_tol,
					executor=executor,
				)
			)

		# Paper claim: carbon-aware shifting reduces carbon vs the agnostic baseline.
		checks.append(
			CarbonReductionCheck(
				name="carbon_aware_reduces_carbon",
				baseline_path=self._summary_path(FIG89_BASELINE),
				baseline_label=FIG89_BASELINE,
				aware_paths=tuple(
					(f, self._summary_path(f)) for f in FIG89_CARBON_AWARE
				),
				executor=executor,
			)
		)

		# Paper claim: allocating reserved instances lowers total dollar cost
		# relative to no reserved instances.
		checks.append(
			ReservedCostReductionCheck(
				name="reserved_instances_reduce_cost",
				baseline_label=FIG11_RESERVED_BASELINE,
				baseline_path=self._summary_path(FIG11_RESERVED_BASELINE),
				steps=tuple((f, self._summary_path(f)) for f in FIG11_RESERVED_STEPS),
				executor=executor,
			)
		)

		return tuple(checks)
