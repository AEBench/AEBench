from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase
from evaluator.oracles.reporting import BaseCheck

from .checks import ExpectedFilesCheck
from .consts import expected_elf_paths


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	"""Verify every benchmark was compiled for both configurations and all opt levels."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			ExpectedFilesCheck(
				name="benchmark_elf_matrix",
				root=self.runtime_path("benchmarks"),
				pattern="*/build-*/*.elf",
				expected_relative_paths=expected_elf_paths(),
			),
		)
