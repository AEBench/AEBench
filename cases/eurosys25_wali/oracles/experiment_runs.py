from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .consts import EXPERIMENT_DIR
from .parsing import (
	BenchmarkLogCheck,
	BenchmarkResultsCheck,
	PdfOutputsCheck,
	PortabilityLogCheck,
	WasiLayeringLogCheck,
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			PortabilityLogCheck(
				name="ported_applications",
				path=self.runtime_path("evaluation", "portability.log"),
			),
			WasiLayeringLogCheck(
				name="libuvwasi_tests",
				path=self.runtime_path("evaluation", "libuvwasi-tests.log"),
			),
			BenchmarkLogCheck(
				name="benchmark_modes",
				path=self.runtime_path(EXPERIMENT_DIR, "benchmark.log"),
			),
			BenchmarkResultsCheck(
				name="benchmark_results",
				results_dir=self.runtime_path(EXPERIMENT_DIR, "results"),
				reference_path=self.ref_path("evaluation.ref.json"),
			),
			PdfOutputsCheck(
				name="generated_benchmark_figures",
				paths=(
					self.runtime_path(EXPERIMENT_DIR, "figures", "memory.pdf"),
					self.runtime_path(EXPERIMENT_DIR, "figures", "runtime_a.pdf"),
					self.runtime_path(EXPERIMENT_DIR, "figures", "runtime_b.pdf"),
					self.runtime_path(EXPERIMENT_DIR, "figures", "runtime_c.pdf"),
				),
			),
		)
