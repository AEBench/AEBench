from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType


_BUILD_MODE_ENV = "AE_KONDO_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0

_BUILD_MODE_VERIFY = "verify"
_BUILD_MODE_COMMAND = "command"
_VALID_BUILD_MODES = {_BUILD_MODE_VERIFY, _BUILD_MODE_COMMAND}

_EXPECTED_BUILD_OUTPUTS: tuple[str, ...] = (
    "local-dafny/Binaries/Dafny.dll",
    "local-dafny/Scripts/dafny",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):
    mode: str

    def check(self) -> utils.CheckResult:
        return utils.CheckResult.failure(
            f"invalid {_BUILD_MODE_ENV}={self.mode!r}; "
            f"expected one of: {', '.join(sorted(_VALID_BUILD_MODES))}"
        )


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    @staticmethod
    def _build_mode() -> str:
        raw = os.environ.get(_BUILD_MODE_ENV, _BUILD_MODE_VERIFY).strip().lower()
        return raw or _BUILD_MODE_VERIFY

    def _expected_output_checks(self) -> tuple[utils.BaseCheck, ...]:
        return tuple(
            FilesystemPathCheck(
                name=f"built_{Path(rel).name}",
                path=self.workspace_path(rel),
                path_type=PathType.FILE,
            )
            for rel in _EXPECTED_BUILD_OUTPUTS
        )

    def requirements(self) -> Sequence[utils.BaseCheck]:
        mode = self._build_mode()

        if mode not in _VALID_BUILD_MODES:
            return (
                InvalidBuildModeCheck(
                    name="valid_build_mode",
                    mode=mode,
                ),
            )

        output_checks = self._expected_output_checks()

        if mode == _BUILD_MODE_COMMAND:
            return (
                BuildCommandCheck(
                    name="build_dafny",
                    cwd=self.workspace_path("local-dafny"),
                    cmd=("make",),
                    timeout_seconds=_BUILD_TIMEOUT_SECONDS,
                ),
                *output_checks,
            )

        return output_checks