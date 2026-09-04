from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            self.version_check(
                name="rustc",
                cmd=("rustc", "--version"),
                min_version=(1, 83, 0),
            ),
            self.version_check(
                name="cargo",
                cmd=("cargo", "--version"),
                min_version=(1, 0, 0),
            ),
            self.version_check(
                name="node",
                cmd=("node", "--version"),
                min_version=(0, 0, 0),
            ),
            self.version_check(
                name="make",
                cmd=("make", "--version"),
                min_version=(0, 0, 0),
                optional=True,
            ),
            self.path_check(
                name="repo_root_exists",
                path=self.workspace_path(),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="datasets_ref_exists",
                path=self.ref_path("datasets.ref.json"),
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="timings_ref_exists",
                path=self.ref_path("timings.ref.json"),
                kind=PathKind.FILE,
            ),
        )
