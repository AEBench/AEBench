from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, utils  # type: ignore[import-untyped]
from evaluator.oracles.checks import BaseCheck  # type: ignore[import-untyped]


class OracleEnvSetup(CaseOracleEnvSetupBase):  # type: ignore[misc]
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
                kind="dir",
            ),
            self.path_check(
                name="datasets_ref_exists",
                path=self.ref_path("datasets.ref.json"),
                kind="file",
            ),
            self.path_check(
                name="timings_ref_exists",
                path=self.ref_path("timings.ref.json"),
                kind="file",
            ),
        )
