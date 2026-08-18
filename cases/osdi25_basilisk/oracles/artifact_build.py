from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

from .consts import DAFNY_DIR

_DAFNY_SMOKE_SIGNATURE = "Dafny program verifier finished with 1 verified, 0 errors"


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Validate the built Dafny/Basilisk driver with a real verifier smoke test."""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="dafny_smoke_verification",
				cmd=("./Scripts/dafny", "/compile:0", "test.dfy"),
				cwd=self.runtime_path(DAFNY_DIR),
				timeout_seconds=180.0,
				signature=_DAFNY_SMOKE_SIGNATURE,
			),
		)
