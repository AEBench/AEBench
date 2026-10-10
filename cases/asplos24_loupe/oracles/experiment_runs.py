from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .common import LoupeRunEvidenceCheck, LoupeTablesCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		reference = self.ref_path("smoke_expectations.ref.json")
		return (
			LoupeTablesCheck(
				name="complete_semantic_syscall_tables",
				workspace=self.workspace_path(),
				reference=reference,
			),
			LoupeRunEvidenceCheck(
				name="complete_execution_evidence",
				workspace=self.workspace_path(),
				reference=reference,
			),
		)
