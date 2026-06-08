from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType


_BUILD_MODE_ENV = "AE_PCS_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0
_IMPORT_TIMEOUT_SECONDS = 30.0

_REQUIRED_PACKAGES = ("numpy", "pandas", "scipy", "ray", "matplotlib", "seaborn")


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):
    mode: str

    def check(self) -> utils.CheckResult:
        return utils.CheckResult.failure(
            f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
        )


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    @staticmethod
    def _build_mode() -> str:
        raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
        return raw or "verify"

    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.paths.workspace_dir
        mode = self._build_mode()

        if mode == "command":
            return (
                BuildCommandCheck(
                    name="run_setup_sh",
                    cwd=repo_root,
                    cmd=("bash", "setup.sh"),
                    timeout_seconds=_BUILD_TIMEOUT_SECONDS,
                ),
            )

        if mode == "verify":
            reqs: list[utils.BaseCheck] = [
                FilesystemPathCheck(
                    name="requirements_txt_exists",
                    path=self.workspace_path("requirements.txt"),
                    path_type=PathType.FILE,
                ),
            ]

            for package in _REQUIRED_PACKAGES:
                reqs.append(
                    BuildCommandCheck(
                        name=f"python_import_{package}",
                        cwd=repo_root,
                        cmd=("python3", "-c", f"import {package}"),
                        timeout_seconds=_IMPORT_TIMEOUT_SECONDS,
                    )
                )

            return tuple(reqs)

        return (
            InvalidBuildModeCheck(
                name="build_mode_valid",
                mode=mode,
            ),
        )