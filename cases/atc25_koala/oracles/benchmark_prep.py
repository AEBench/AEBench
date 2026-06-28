from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.utils import BaseCheck

from .consts import BENCHMARKS, SPEC_SCRIPTS


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Verify each selected benchmark is staged & runnable inside the koala image.

	Paths are relative to app_dir = /koala. Koala's Dockerfile installs deps but
	does not fetch inputs (main.sh fetches them at run time), so we check the
	benchmark sources are present rather than inputs/; input correctness is
	exercised by experiment_runs.

	We check the benchmark directory and its five Koala support scripts
	(install/fetch/execute/validate/clean.sh) — the uniform contract across all
	sets. We do NOT require a scripts/ subdir: 10 sets keep their computation in
	scripts/, but ci-cd uses riker/ + makeself/ instead. The computation scripts
	are exercised by execute.sh in experiment_runs.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = []

		for bench in BENCHMARKS:
			checks.append(
				self.path_check(
					name=f"{bench}_dir",
					path=self.app_path(bench),
					kind=PathKind.DIRECTORY,
				)
			)
			for script in SPEC_SCRIPTS:
				checks.append(
					self.path_check(
						name=f"{bench}_{script}",
						path=self.app_path(bench, script),
						kind=PathKind.FILE,
					)
				)

		return tuple(checks)
