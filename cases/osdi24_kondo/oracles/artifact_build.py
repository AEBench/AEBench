from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.reporting import BaseCheck

_DAFNY_CHECK_TIMEOUT_SECONDS = 120.0
_DAFNY_CHECK_SIGNATURE = "Dafny program verifier finished with 1 verified, 0 errors"

_EXPECTED_BUILD_OUTPUTS: tuple[str, ...] = (
	"local-dafny/Binaries/Dafny.dll",
	"local-dafny/Scripts/dafny",
	"local-dafny/Binaries/z3/bin/z3-4.8.5",
	"local-dafny/Binaries/z3/bin/z3-4.12.1",
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def _expected_output_checks(self) -> tuple[BaseCheck, ...]:
		return tuple(
			self.path_check(
				name=f"built_{Path(rel).name}",
				path=self.workspace_path(rel),
				kind=PathKind.FILE,
			)
			for rel in _EXPECTED_BUILD_OUTPUTS
		)

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			*self._expected_output_checks(),
			self.command_check(
				name="dafny_runs_as_expected",
				cmd=(
					"./local-dafny/Scripts/dafny",
					"/compile:0",
					"local-dafny/test.dfy",
				),
				timeout_seconds=_DAFNY_CHECK_TIMEOUT_SECONDS,
				signature=_DAFNY_CHECK_SIGNATURE,
			),
		)
