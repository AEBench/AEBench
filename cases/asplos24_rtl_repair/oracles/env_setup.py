from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def _eda_tool_cmd(self, tool: str, *args: str) -> tuple[str, ...]:
        candidates: list[Path] = []

        for env_name in ("OSS_CAD_SUITE",):
            raw = os.environ.get(env_name, "").strip()
            if not raw:
                continue
            path = Path(raw).expanduser()
            candidates.append(path / "bin" if (path / "bin").is_dir() else path)

        bundled = self.case_path(".oss-cad-suite", "bin")
        if bundled.is_dir():
            candidates.append(bundled)

        for bin_dir in candidates:
            executable = bin_dir / tool
            if executable.is_file():
                return (str(executable), *args)

        return (tool, *args)

    def requirements(self) -> Sequence[BaseCheck]:
        return (
            self.version_check(
                name="python3_version",
                cmd=("python3", "--version"),
                min_version=(3, 10, 0),
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="cargo",
                cmd=("cargo", "--version"),
                min_version=(1, 60, 0),
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="bitwuzla",
                cmd=self._eda_tool_cmd("bitwuzla", "--version"),
                min_version=(1, 0, 0),
                max_version=(1, 0, 0),
                version_regex=r"1\.0-prerelease",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="verilator",
                cmd=self._eda_tool_cmd("verilator", "--version"),
                min_version=(4, 0, 0),
                max_version=(4, 999, 999),
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="iverilog",
                cmd=self._eda_tool_cmd("iverilog", "-V"),
                min_version=(12, 0, 0),
                version_regex=r"Icarus Verilog version 12\.0",
                timeout_seconds=10.0,
            ),
            self.version_check(
                name="yosys",
                cmd=self._eda_tool_cmd("yosys", "-version"),
                min_version=(0, 9, 0),
                timeout_seconds=10.0,
            ),
            self.path_check(
                name="artifact_root_exists",
                path=self.artifact_path(),
                kind=PathKind.DIRECTORY,
            ),
        )
