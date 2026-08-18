from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    """Verify the experiment inputs and benchmark datasets are present."""

    def requirements(self) -> Sequence[BaseCheck]:
        return (
            self.path_check(
                name="qbenchmark_dir_exists",
                path=self.workspace_path("data/Qbenchmark"),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="examples_dir_exists",
                path=self.workspace_path("examples"),
                kind=PathKind.DIRECTORY,
            ),
        )
