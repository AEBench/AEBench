from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

_DAFNY_CHECK_TIMEOUT_SECONDS = 120.0
_DAFNY_CHECK_SIGNATURE = "Dafny program verifier finished with 1 verified, 0 errors"


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
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
