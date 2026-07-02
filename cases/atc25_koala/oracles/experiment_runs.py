from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind
from evaluator.oracles.utils import BaseCheck

from .checks import KoalaCorrectnessCheck, KoalaPassLogCheck
from .consts import (
	BENCHMARKS,
	BENCHMARKS_REF,
	OPTIONAL_BENCHMARKS,
	RESULTS_DIR,
	hash_filename,
	log_filename,
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the agent-saved Koala outputs under script-results/.

	For each of the 11 benchmarks (run on --min): the harness reported [pass]
	(and never [fail]), and the saved <bench>.hash shows every validate.sh status
	line at 0 with the expected count. Dynamic metrics are ignored.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		ref = json.loads(self.ref_path(BENCHMARKS_REF).read_text(encoding="utf-8"))
		expected = ref["expected_status_lines"]

		checks: list[BaseCheck] = []

		# The results directory itself must exist.
		checks.append(
			self.path_check(
				name="results_dir_exists",
				path=self.artifact_path(RESULTS_DIR),
				kind=PathKind.DIRECTORY,
			)
		)

		for bench in BENCHMARKS:
			# Optional benchmarks still run and are logged, but a failure is
			# reported as a warning rather than sinking the phase.
			optional = bench in OPTIONAL_BENCHMARKS
			checks.append(
				KoalaPassLogCheck(
					name=f"{bench}_pass_log",
					path=self.artifact_path(RESULTS_DIR, log_filename(bench)),
					bench=bench,
					optional=optional,
				)
			)
			checks.append(
				KoalaCorrectnessCheck(
					name=f"{bench}_correctness",
					path=self.artifact_path(RESULTS_DIR, hash_filename(bench)),
					expected_lines=int(expected[bench]),
					optional=optional,
				)
			)

		return tuple(checks)
