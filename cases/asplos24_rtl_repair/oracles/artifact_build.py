from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathKind

_BUILD_MODE_ENV = "AE_RTL_REPAIR_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 1800.0
_IMPORT_TIMEOUT_SECONDS = 30.0

_REQUIRED_PACKAGES = ("tomli", "ply", "vcdvcd", "jinja2", "psutil")
_BUILD_ARTIFACTS = (
    "synth/target/release/synth",
    "scripts/osdd/target/release/osdd",
)


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

    @staticmethod
    def _python_executable(repo_root: Path) -> str:
        venv_python = repo_root / "venv" / "bin" / "python"
        if venv_python.is_file():
            return str(venv_python)
        return "python3"

    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.workspace_path()
        python = self._python_executable(repo_root)
        mode = self._build_mode()

        if mode == "command":
            venv_python = str(repo_root / "venv" / "bin" / "python")
            return (
                self.command_check(
                    name="create_venv",
                    cwd=repo_root,
                    cmd=("python3", "-m", "venv", "venv"),
                    timeout_seconds=120.0,
                ),
                self.command_check(
                    name="pip_install_requirements",
                    cwd=repo_root,
                    cmd=(venv_python, "-m", "pip", "install", "-r", "requirements.txt"),
                    timeout_seconds=_BUILD_TIMEOUT_SECONDS,
                ),
                self.command_check(
                    name="build_synth_binary",
                    cwd=repo_root / "synth",
                    cmd=("cargo", "build", "--release"),
                    timeout_seconds=_BUILD_TIMEOUT_SECONDS,
                ),
                self.command_check(
                    name="build_osdd_binary",
                    cwd=repo_root / "scripts" / "osdd",
                    cmd=("cargo", "build", "--release"),
                    timeout_seconds=_BUILD_TIMEOUT_SECONDS,
                ),
            )

        if mode == "verify":
            reqs: list[utils.BaseCheck] = [
                self.path_check(
                    name="requirements_txt_exists",
                    path=repo_root / "requirements.txt",
                    kind=PathKind.FILE,
                ),
                self.path_check(
                    name="artifact_venv_exists",
                    path=repo_root / "venv" / "bin" / "python",
                    kind=PathKind.FILE,
                ),
                self.command_check(
                    name="verify_rtlrepair_syntax",
                    cwd=repo_root,
                    cmd=(python, "-m", "py_compile", "rtlrepair.py"),
                    timeout_seconds=_IMPORT_TIMEOUT_SECONDS,
                ),
            ]

            for package in _REQUIRED_PACKAGES:
                reqs.append(
                    self.command_check(
                        name=f"python_import_{package}",
                        cwd=repo_root,
                        cmd=(python, "-c", f"import {package}"),
                        timeout_seconds=_IMPORT_TIMEOUT_SECONDS,
                    )
                )

            for rel_path in _BUILD_ARTIFACTS:
                safe_name = rel_path.replace("/", "_").replace(".", "_")
                reqs.append(
                    self.path_check(
                        name=f"built_{safe_name}",
                        path=repo_root / rel_path,
                        kind=PathKind.FILE,
                    )
                )

            return tuple(reqs)

        return (
            InvalidBuildModeCheck(
                name="build_mode_valid",
                mode=mode,
            ),
        )
