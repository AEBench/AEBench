from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.oracle_checks_runtime import OraclePath, RuntimePath
from evaluator.oracles.reporting import BaseCheck

from .common import TOOL_LOOKUP_ENV, TOOL_LOOKUP_MODES, InvalidModeCheck, ToolLookup, env_mode


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def _oss_cad_suite_explicit_dirs(self, lookup: ToolLookup) -> list[OraclePath]:
		dirs: list[OraclePath] = []

		oss_cad_suite = lookup.env_dir("OSS_CAD_SUITE")
		if oss_cad_suite is not None:
			dirs.append(RuntimePath(oss_cad_suite.value / "bin"))
			dirs.append(oss_cad_suite)

		dirs.append(self.workspace_path(".oss-cad-suite", "bin"))
		dirs.append(self.case_path(".oss-cad-suite", "bin"))
		return dirs

	def requirements(self) -> Sequence[BaseCheck]:
		mode = env_mode(TOOL_LOOKUP_ENV, "relaxed")
		if mode not in TOOL_LOOKUP_MODES:
			return (
				InvalidModeCheck(
					name="tool_lookup_mode_valid",
					env_name=TOOL_LOOKUP_ENV,
					mode=mode,
					expected=TOOL_LOOKUP_MODES,
				),
			)

		lookup = ToolLookup(self.executor, workspace_dir=self.workspace_path())
		oss_explicit = self._oss_cad_suite_explicit_dirs(lookup)
		oss_fallback = lookup.oss_cad_suite_dirs() if mode == "relaxed" else []

		def eda_tool_cmd(tool: str, *args: str) -> tuple[str, ...]:
			return lookup.command(
				tool,
				*args,
				mode=mode,
				explicit_dirs=oss_explicit,
				fallback_dirs=oss_fallback,
			)

		# Executables are resolved through a login shell, so in strict mode a
		# rustup install passes once ~/.profile sources ~/.cargo/env; relaxed
		# mode also finds ~/.cargo/bin and $CARGO_HOME/bin directly.
		cargo_cmd = lookup.command(
			"cargo",
			"--version",
			mode=mode,
			fallback_dirs=lookup.cargo_dirs() if mode == "relaxed" else (),
		)

		return (
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 10, 0),
				timeout_seconds=10.0,
			),
			self.version_check(
				name="cargo",
				cmd=cargo_cmd,
				min_version=(1, 60, 0),
				timeout_seconds=10.0,
			),
			self.version_check(
				name="bitwuzla",
				cmd=eda_tool_cmd("bitwuzla", "--version"),
				min_version=(1, 0, 0),
				max_version=(1, 0, 0),
				version_regex=r"1\.0-prerelease",
				timeout_seconds=10.0,
			),
			self.version_check(
				name="verilator",
				cmd=eda_tool_cmd("verilator", "--version"),
				min_version=(4, 0, 0),
				max_version=(4, 999, 999),
				timeout_seconds=10.0,
			),
			self.version_check(
				name="iverilog",
				cmd=eda_tool_cmd("iverilog", "-V"),
				min_version=(12, 0, 0),
				version_regex=r"Icarus Verilog version 12\.0",
				timeout_seconds=10.0,
			),
			self.version_check(
				name="yosys",
				cmd=eda_tool_cmd("yosys", "-version"),
				min_version=(0, 9, 0),
				timeout_seconds=10.0,
			),
			self.path_check(
				name="artifact_root_exists",
				path=self.workspace_path(),
				kind=PathKind.DIRECTORY,
			),
		)
