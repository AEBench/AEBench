from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathCheck, PathKind
from evaluator.oracles.reporting import BaseCheck

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    """Verify the experiment inputs and benchmark datasets are present."""

    def requirements(self) -> Sequence[BaseCheck]:
        return (
            PathCheck(
                name="qbenchmark_dir_exists",
                path=self.runtime_path("data", "Qbenchmark"),
                kind=PathKind.DIRECTORY,
            ),
            PathCheck(
                name="examples_dir_exists",
                path=self.runtime_path("examples"),
                kind=PathKind.DIRECTORY,
            ),
        )
