from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import BENCHMARK_APPS, EXPERIMENT_DIR


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.path_check(
				name=f"released_{filename.replace('.', '_')}",
				path=self.runtime_path(EXPERIMENT_DIR, filename),
				kind=PathKind.FILE,
			)
			for filename in ("benchmarks.tar.gz", "data.tar.gz")
		]
		for app in BENCHMARK_APPS:
			for filename in (
				app,
				f"{app}.aot",
				"Makefile",
				f"{app}.dockerfile",
				f"{app}.btime.dockerfile",
			):
				checks.append(
					self.path_check(
						name=f"{app}_{filename.replace('.', '_')}",
						path=self.runtime_path(EXPERIMENT_DIR, "benchmarks", app, filename),
						kind=PathKind.FILE,
					)
				)
			checks.append(
				self.path_check(
					name=f"{app}_data",
					path=self.runtime_path(EXPERIMENT_DIR, "benchmarks", app, "data"),
					kind=PathKind.DIRECTORY,
				)
			)
		return tuple(checks)
