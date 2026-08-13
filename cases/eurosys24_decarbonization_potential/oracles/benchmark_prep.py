from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	COMBINED_CARBON_PATH,
	LATENCY_MATRIX_PATH,
	SCOPED_SCRIPTS,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Verify the experiment inputs are staged: the committed carbon-intensity
	trace and GCP latency matrix (in shared_data), plus each scoped experiment's
	calculate script.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.path_check(
				name="combined_carbon_csv",
				path=self.runtime_path(COMBINED_CARBON_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="gcp_latency_matrix_csv",
				path=self.runtime_path(LATENCY_MATRIX_PATH),
				kind=PathKind.FILE,
			),
		]
		for exp_dir, script in SCOPED_SCRIPTS:
			rel_path = f"{exp_dir}/{script}"
			checks.append(
				self.path_check(
					name=f"script_{rel_path.removesuffix('.py').replace('/', '_')}",
					path=self.runtime_path(rel_path),
					kind=PathKind.FILE,
				)
			)
		return tuple(checks)
