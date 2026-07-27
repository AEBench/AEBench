from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .common import (
    CMAKE_MIN_VERSION,
    GPP_MIN_VERSION,
    REQUIRED_SCRIPTS,
    RUSTC_MIN_VERSION,
    find_repo_root,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = find_repo_root(self.workspace_path())

        checks: list[BaseCheck] = [
            self.version_check(
                name="rustc_version",
                cmd=("rustc", "--version"),
                min_version=RUSTC_MIN_VERSION,
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="cargo_version",
                cmd=("cargo", "--version"),
                min_version=(1, 80, 0),
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="cmake_version",
                cmd=("cmake", "--version"),
                min_version=CMAKE_MIN_VERSION,
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="gpp_version",
                cmd=("g++", "--version"),
                min_version=GPP_MIN_VERSION,
                timeout_seconds=30.0,
                # Apple Clang reports as g++ on macOS hosts; Ubuntu 22.04 AE env is preferred.
                optional=True,
            ),
            self.path_check(
                name="repo_root_exists",
                path=repo_root,
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="readme_exists",
                path=repo_root / "README.md",
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="cargo_toml_exists",
                path=repo_root / "Cargo.toml",
                kind=PathKind.FILE,
            ),
        ]

        for rel in REQUIRED_SCRIPTS:
            safe = rel.replace(".", "_").replace("/", "_")
            checks.append(
                self.path_check(
                    name=f"script_{safe}",
                    path=repo_root / rel,
                    kind=PathKind.FILE,
                )
            )

        return tuple(checks)
