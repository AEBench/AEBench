
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleArtifactBuildBase, utils  # type: ignore[import-untyped]
from evaluator.oracles.checks import BaseCheck  # type: ignore[import-untyped]

_BUILD_COMMAND: tuple[str, ...] = (
    "make",
    "-j8",
    "tools/diamond-types/target/release/dt",
    "tools/crdt-converter/target/release/crdt-converter",
    "tools/diamond-types/target/release/paper-stats",
    "tools/paper-benchmarks/target/memusage/paper-benchmarks",
    "tools/paper-benchmarks/target/release/paper-benchmarks",
    "tools/ot-bench/target/memusage/ot-bench",
    "tools/ot-bench/target/release/ot-bench",
)

_EXPECTED_BUILD_OUTPUTS = _BUILD_COMMAND[2:]
_BUILD_MODE_ENV = "AE_EGWALKER_BUILD_MODE"


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):  # type: ignore[misc]
    mode: str

    def check(self) -> utils.CheckResult:
        return utils.CheckResult.failure(
            f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
        )


class OracleArtifactBuild(CaseOracleArtifactBuildBase):  # type: ignore[misc]
    def requirements(self) -> Sequence[BaseCheck]:
        mode = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower() or "verify"

        if mode == "command":
            return (
                self.command_check(
                    name="artifact_core_make_tools",
                    cwd=self.workspace_path(),
                    cmd=_BUILD_COMMAND,
                    timeout_seconds=300.0,
                ),
            )

        if mode == "verify":
            return tuple(
                self.path_check(
                    name=f"built_output_{Path(rel_path).name}",
                    path=self.workspace_path(rel_path),
                    kind="file",
                )
                for rel_path in _EXPECTED_BUILD_OUTPUTS
            )

        return (InvalidBuildModeCheck(name="build_mode_valid", mode=mode),)
