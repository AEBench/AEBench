from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .common import TARGETS


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			*(
				self.path_check(name=path, path=self.runtime_path(path), kind=PathKind.FILE)
				for path in (
					"ASPLOS25.md",
					"infra/platforms-eval.sh",
					"infra/platforms/cross-plot.py",
					*(f"src/platforms/{source}.rkt" for source in TARGETS.values()),
				)
			),
			*(
				self.min_matching_entry_count_check(
					name=f"{suite}_benchmarks",
					directory=self.runtime_path("bench", suite),
					pattern="*.fpcore",
					min_count=count,
				)
				for suite, count in (("hamming", 6), ("mathematics", 11))
			),
		)
