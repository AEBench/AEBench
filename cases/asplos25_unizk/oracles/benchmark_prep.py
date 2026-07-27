from __future__ import annotations

import re
from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
    REQUIRED_CONFIGS,
    REQUIRED_DIRECTORIES,
    find_repo_root,
)

_REQUIRED_EXAMPLES = (
    "examples/factorial.rs",
    "examples/fibonacci.rs",
    "examples/mvm.rs",
    "examples/sha256.rs",
    "examples/ecdsa.rs",
    "examples/img_crop.rs",
    "examples/aes_starky.rs",
    "examples/aes_starky_recursive.rs",
    "examples/sha256_starky.rs",
    "examples/sha256_starky_recursive.rs",
    "examples/fib_starky.rs",
    "examples/fib_starky_recursive.rs",
    "examples/fac_starky.rs",
    "examples/fac_starky_recursive.rs",
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

        for rel in REQUIRED_CONFIGS:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", rel).strip("_") or "cfg"
            checks.append(
                self.path_check(
                    name=f"cfg_{safe}",
                    path=repo_root / rel,
                    kind=PathKind.FILE,
                )
            )

        for rel in _REQUIRED_EXAMPLES:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", rel).strip("_") or "ex"
            checks.append(
                self.path_check(
                    name=f"example_{safe}",
                    path=repo_root / rel,
                    kind=PathKind.FILE,
                )
            )

        return tuple(checks)
