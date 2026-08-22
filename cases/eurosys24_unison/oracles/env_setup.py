from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
    CMAKE_MIN_VERSION,
    GIT_MIN_VERSION,
    GPP_MIN_VERSION,
    MPI_MIN_VERSION,
    MPI_VERSION_REGEX,
    PYTHON_MIN_VERSION,
    REQUIRED_SCRIPTS,
    find_repo_root,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = find_repo_root(self.workspace_path())

        checks: list[BaseCheck] = [
            # Versioned toolchain checks for every README software dependency we
            # can probe locally (Python, g++/clang, Git, CMake, OpenMPI).
            self.version_check(
                name="python3_version",
                cmd=("python3", "--version"),
                min_version=PYTHON_MIN_VERSION,
                version_regex=r"Python\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="cmake_version",
                cmd=("cmake", "--version"),
                min_version=CMAKE_MIN_VERSION,
                version_regex=r"cmake version\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="gpp_version",
                cmd=("g++", "--version"),
                min_version=GPP_MIN_VERSION,
                # Parses GNU g++ ("g++ (Ubuntu …) 11.4.0") and Apple Clang via g++.
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="git_version",
                cmd=("git", "--version"),
                min_version=GIT_MIN_VERSION,
                version_regex=r"git version\s+([0-9]+(?:\.[0-9]+){1,2})",
                timeout_seconds=30.0,
            ),
            self.version_check(
                name="mpirun_version",
                cmd=("mpirun", "--version"),
                min_version=MPI_MIN_VERSION,
                version_regex=MPI_VERSION_REGEX,
                timeout_seconds=30.0,
                # Functional accuracy (Unison MTP vs default) does not need MPI;
                # distributed / barrier / nullmsg experiments do.
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
