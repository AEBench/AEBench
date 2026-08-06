from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import BENCHMARK_APPS, EXPERIMENT_DIR
from .parsing import ExperimentInputsCheck


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		experiment_dir = self.runtime_path(EXPERIMENT_DIR)
		checks: list[BaseCheck] = [
			ExperimentInputsCheck(
				name="released_experiment_inputs",
				experiment_dir=experiment_dir,
				reference_path=self.ref_path("inputs.ref.json"),
				executor=self.executor,
			),
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
