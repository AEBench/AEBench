from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import (
    BUILD_MODE_ENV,
    BUILD_TIMEOUT_SECONDS,
    ENABLE_MODULES,
    FAT_TREE_BINARY_DIR,
    FAT_TREE_BINARY_GLOB,
    MAKE_JOBS_CAP,
    MTP_LIBRARY_DIR,
    MTP_LIBRARY_GLOB,
    find_repo_root,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(BaseCheck):
    mode: str

    def check(self) -> CheckResult:
        return CheckResult.failure(
            f"invalid {BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GlobPresentCheck(BaseCheck):
    """Fail unless at least one path matching pattern exists under directory."""

    directory: Path
    pattern: str
    missing_message: str

    def check(self) -> CheckResult:
        if not self.directory.is_dir():
            return CheckResult.failure(f"missing directory: {self.directory}")
        matches = sorted(path for path in self.directory.glob(self.pattern) if path.is_file())
        if not matches:
            return CheckResult.failure(
                f"{self.missing_message} (no {self.pattern!r} under {self.directory})"
            )
        return CheckResult.success(message=f"found {matches[0].name}")


def _make_jobs() -> int:
    """Bound ns-3 / ninja parallelism; exp.py uses unbounded build parallelism."""
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
        binary_dir = repo_root / FAT_TREE_BINARY_DIR

        if mode == "command":
            # Upstream README / exp.py:
            #   ./ns3 configure -d optimized --enable-modules=... --enable-mtp
            #   ./ns3 build fat-tree
            jobs = _make_jobs()
            return (
                self.command_check(
                    name="ns3_configure_mtp",
                    cwd=repo_root,
                    cmd=(
                        "./ns3",
                        "configure",
                        "-d",
                        "optimized",
                        f"--enable-modules={ENABLE_MODULES}",
                        "--enable-mtp",
                    ),
                    timeout_seconds=BUILD_TIMEOUT_SECONDS,
                ),
                self.command_check(
                    name="ns3_build_fat_tree",
                    cwd=repo_root,
                    cmd=("./ns3", "build", "fat-tree", "-j", str(jobs)),
                    timeout_seconds=BUILD_TIMEOUT_SECONDS,
                ),
                GlobPresentCheck(
                    name="fat_tree_binary_built",
                    directory=binary_dir,
                    pattern=FAT_TREE_BINARY_GLOB,
                    missing_message="fat-tree optimized binary not built",
                ),
                GlobPresentCheck(
                    name="mtp_library_built",
                    directory=repo_root / MTP_LIBRARY_DIR,
                    pattern=MTP_LIBRARY_GLOB,
                    missing_message="Unison MTP library not built (--enable-mtp)",
                ),
            )

        if mode == "verify":
            return (
                self.path_check(
                    name="ns3_driver_exists",
                    path=repo_root / "ns3",
                    kind=PathKind.FILE,
                ),
                self.path_check(
                    name="mtp_module_exists",
                    path=repo_root / "src" / "mtp" / "CMakeLists.txt",
                    kind=PathKind.FILE,
                ),
                self.path_check(
                    name="build_dir_exists",
                    path=repo_root / "build",
                    kind=PathKind.DIRECTORY,
                ),
                GlobPresentCheck(
                    name="fat_tree_binary_built",
                    directory=binary_dir,
                    pattern=FAT_TREE_BINARY_GLOB,
                    missing_message="fat-tree optimized binary not built",
                ),
                GlobPresentCheck(
                    name="mtp_library_built",
                    directory=repo_root / MTP_LIBRARY_DIR,
                    pattern=MTP_LIBRARY_GLOB,
                    missing_message="Unison MTP library not built (--enable-mtp)",
                ),
            )

        return (
            InvalidBuildModeCheck(
                name="build_mode_valid",
                mode=mode,
            ),
        )
