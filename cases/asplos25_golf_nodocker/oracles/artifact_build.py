from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.checks import VersionCheck
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import (
	BASELINE_BINARY_PATH,
	GO_TOOLCHAIN_VERSION,
	GO_VERSION_REGEX,
	GOLF_BINARY_PATH,
	TESTER_BINARY_PATH,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolchainVersionCheck(BaseCheck):
	"""Fail unless a workspace-built `go` runs and reports exactly the expected version."""

	binary: RuntimePath
	version: tuple[int, int, int]

	def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
		# Resolve against the target's workspace: a bare "./golf/bin/go" would be looked up
		# relative to the evaluator's own working directory on the local target.
		binary = str(executor.resolve_path(self.binary))
		return VersionCheck(
			name=self.name,
			cmd=(binary, "version"),
			min_version=self.version,
			max_version=self.version,
			version_regex=GO_VERSION_REGEX,
		).check(executor)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence:
		return (
			self.path_check(
				name="golf_binary",
				path=self.runtime_path(GOLF_BINARY_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="baseline_binary",
				path=self.runtime_path(BASELINE_BINARY_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="tester_binary",
				path=self.runtime_path(TESTER_BINARY_PATH),
				kind=PathKind.FILE,
			),
			# Run each toolchain to show the build works, not just that a file exists.
			ToolchainVersionCheck(
				name="golf_toolchain_runs",
				binary=self.runtime_path(GOLF_BINARY_PATH),
				version=GO_TOOLCHAIN_VERSION,
			),
			ToolchainVersionCheck(
				name="baseline_toolchain_runs",
				binary=self.runtime_path(BASELINE_BINARY_PATH),
				version=GO_TOOLCHAIN_VERSION,
			),
		)
