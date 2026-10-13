from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

_BENCHMARK_INPUTS = (
	"benchmark/uprobe/benchmark.py",
	"benchmark/uprobe/uprobe",
	"benchmark/uprobe/.output/uprobe.bpf.o",
	"benchmark/syscall/benchmark.py",
	"benchmark/syscall/syscall",
	"benchmark/syscall/victim",
	"benchmark/syscall/.output/syscall.bpf.o",
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return tuple(
			self.path_check(
				name=f"prepared_{path.replace('/', '_').replace('.', '_')}",
				path=self.runtime_path(path),
				kind=PathKind.FILE,
			)
			for path in _BENCHMARK_INPUTS
		)
