from __future__ import annotations

from collections.abc import Sequence
from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathCheck, PathKind

from .common import find_outputs_dir

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        checks: list[BaseCheck] = []

        #output folder created (located by its contents, wherever the agent made it)
        outputs_dir = find_outputs_dir(self.workspace_path(), executor=self.executor)
        checks.append(
            PathCheck(
                name="local_outputs_dir_exists",
                path=outputs_dir or self.workspace_path() / "outputs",
                kind=PathKind.DIRECTORY,
            )
        )
        return tuple(checks)