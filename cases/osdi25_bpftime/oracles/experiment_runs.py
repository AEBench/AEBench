from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .parsing import BpftimeTable3Check

_UPROBE_RESULTS = "benchmark/uprobe/benchmark-output.json"
_UPROBE_LOG = "benchmark/uprobe/benchmark-logs.txt"
_SYSCALL_RESULTS_GLOB = "syscall_benchmark_results_*.json"


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			BpftimeTable3Check(
				name="table3_microbenchmarks",
				uprobe_results=self.runtime_path(_UPROBE_RESULTS),
				uprobe_log=self.runtime_path(_UPROBE_LOG),
				syscall_dir=self.runtime_path("benchmark/syscall"),
				syscall_pattern=_SYSCALL_RESULTS_GLOB,
				reference_path=self.ref_path("table3.paper.json"),
			),
		)
