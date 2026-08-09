from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import (
    BUILD_MODE_ENV,
    BUILD_TIMEOUT_SECONDS,
    MAKE_JOBS_CAP,
    RAMULATOR_RELPATH,
    find_repo_root,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(BaseCheck):
    mode: str

    def check(self) -> CheckResult:
        return CheckResult.failure(
            f"invalid {BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
        )


def _make_jobs() -> int:
    """Bound make parallelism; README uses unbounded `make -j`."""
    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, MAKE_JOBS_CAP))


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    @staticmethod
    def _build_mode() -> str:
        raw = os.environ.get(BUILD_MODE_ENV, "verify").strip().lower()
        return raw or "verify"

    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = find_repo_root(self.workspace_path())
        mode = self._build_mode()
        ramsim_dir = repo_root / "thirdparty" / "ramsim"
        build_dir = ramsim_dir / "build"
        ramulator = repo_root / RAMULATOR_RELPATH

        if mode == "command":
            # Upstream README:
            #   cd thirdparty/ramsim && mkdir build && cd build && cmake .. && make -j
            jobs = _make_jobs()
            return (
                self.command_check(
                    name="mkdir_ramsim_build",
                    cwd=ramsim_dir,
                    cmd=("mkdir", "-p", "build"),
                    timeout_seconds=30.0,
                ),
                self.command_check(
                    name="cmake_configure_ramsim",
                    cwd=build_dir,
                    cmd=("cmake", ".."),
                    timeout_seconds=BUILD_TIMEOUT_SECONDS,
                ),
                self.command_check(
                    name="make_ramsim",
                    cwd=build_dir,
                    cmd=("make", f"-j{jobs}"),
                    timeout_seconds=BUILD_TIMEOUT_SECONDS,
                ),
                self.path_check(
                    name="ramulator2_built",
                    path=ramulator,
                    kind=PathKind.FILE,
                ),
            )

        if mode == "verify":
            return (
                self.path_check(
                    name="ramsim_sources_exist",
                    path=ramsim_dir / "CMakeLists.txt",
                    kind=PathKind.FILE,
                ),
                self.path_check(
                    name="ramsim_build_dir_exists",
                    path=build_dir,
                    kind=PathKind.DIRECTORY,
                ),
                self.path_check(
                    name="ramulator2_built",
                    path=ramulator,
                    kind=PathKind.FILE,
                ),
            )

        return (
            InvalidBuildModeCheck(
                name="build_mode_valid",
                mode=mode,
            ),
        )
