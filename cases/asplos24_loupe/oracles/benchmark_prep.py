from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .common import PinnedDatabaseCheck, PinnedInputsCheck


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		reference = self.ref_path("smoke_expectations.ref.json")
		return (
			PinnedInputsCheck(
				name="pinned_loupe_inputs",
				workspace=self.workspace_path(),
				reference=reference,
			),
			self.path_check(
				name="loupedb_checkout",
				path=self.workspace_path("loupedb", ".git"),
				kind=PathKind.DIRECTORY,
			),
			PinnedDatabaseCheck(
				name="pinned_loupedb",
				workspace=self.workspace_path(),
				reference=reference,
			),
		)
