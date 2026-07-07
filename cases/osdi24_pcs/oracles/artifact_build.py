from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind


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
        repo_root = self.workspace_path()
        mode = self._build_mode()

        if mode == "command":
            return (
                self.command_check(
                    name="run_setup_sh",
                    cwd=repo_root,
                    cmd=("bash", "setup.sh"),
                    timeout_seconds=_BUILD_TIMEOUT_SECONDS,
                ),
            )

        if mode == "verify":
            reqs: list[utils.BaseCheck] = [
                self.path_check(
                    name="requirements_txt_exists",
                    path=self.workspace_path("requirements.txt"),
                    kind=PathKind.FILE,
                ),
            ]

            for package in _REQUIRED_PACKAGES:
                reqs.append(
                    self.command_check(
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