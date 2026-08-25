from __future__ import annotations

import re
from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
    REQUIRED_DIRECTORIES,
    REQUIRED_SCRATCH,
    find_repo_root,
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = find_repo_root(self.workspace_path())
        checks: list[BaseCheck] = []

        for rel in REQUIRED_DIRECTORIES:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", rel).strip("_") or "dir"
            checks.append(
                self.path_check(
                    name=f"dir_{safe}",
                    path=repo_root / rel,
                    kind=PathKind.DIRECTORY,
                )
            )

        for rel in REQUIRED_SCRATCH:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", rel).strip("_") or "scratch"
            checks.append(
                self.path_check(
                    name=f"scratch_{safe}",
                    path=repo_root / rel,
                    kind=PathKind.FILE,
                )
            )

        return tuple(checks)
