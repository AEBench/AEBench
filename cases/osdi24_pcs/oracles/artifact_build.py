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

_REQUIRED_PACKAGES = ("numpy", "pandas", "scipy", "ray", "matplotlib", "seaborn")


@dataclass(frozen=True, slots=True, kw_only=True)
class PythonPackageImportableCheck(utils.BaseCheck):
    """Fail if a Python package cannot be imported."""

    package_name: str
    executor: utils.RuntimeCheckExecutor | None = None

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
        try:
            proc = utils.run_check_process_capture(
                cmd=("python3", "-c", f"import {self.package_name}"),
                cwd=None,
                env=None,
                timeout_seconds=30.0,
                capture_limit_chars=utils.DEFAULT_MAX_CAPTURE_CHARS,
                executor=self.executor,
            )
        except (OSError, RuntimeError) as exc:
            return utils.CheckResult.failure(
                f"failed to check import of {self.package_name}: {exc}"
            )

        if proc.timed_out:
            return utils.CheckResult.failure(
                f"import check for {self.package_name} timed out"
            )

        if proc.returncode != 0:
            return utils.CheckResult.failure(
                f"python3 cannot import {self.package_name}",
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
            )

        return utils.CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):
    mode: str

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
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
                    name="requirements_txt",
                    path=repo_root / "requirements.txt",
                    path_type=PathType.FILE,
                ),
            ]
            for pkg in _REQUIRED_PACKAGES:
                reqs.append(
                    PythonPackageImportableCheck(
                        name=f"package_{pkg}",
                        package_name=pkg,
                    )
                )
            return tuple(reqs)

        return (
            InvalidBuildModeCheck(
                name="build_mode_valid",
                mode=mode,
            ),
        )
