from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import PROTOCOLS
from .parsing import SlocReferenceCheck, VerifyAllLogCheck

_COMMON_AUTOGEN_FILES = (
	"applicationProofDraftAutogen.dfy",
	"messageInvariantsAutogen.dfy",
	"monotonicityInvariantsAutogen.dfy",
)
_OWNERSHIP_PROTOCOLS = frozenset({"distributedLock", "shardedKv", "shardedKvBatched", "lockServer"})


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate protocol verification, generated invariants, and SLOC results."""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			VerifyAllLogCheck(
				name="verify_all_succeeded",
				path=self.runtime_path("kondoPrototypes/verify-all.log"),
			),
			SlocReferenceCheck(
				name="sloc_values_match",
				sloc_csv_path=self.runtime_path("kondoPrototypes/evaluation/sloc.csv"),
				reference_path=self.ref_path("sloc.ref.json"),
			),
		]

		for protocol in PROTOCOLS:
			generated_files = list(_COMMON_AUTOGEN_FILES)
			if protocol in _OWNERSHIP_PROTOCOLS:
				generated_files.append("ownershipInvariantsAutogen.dfy")

			for filename in generated_files:
				checks.append(
					self.path_check(
						name=f"{protocol}_{filename.removesuffix('.dfy')}",
						path=self.runtime_path(
							"kondoPrototypes", protocol, "async-kondo", filename
						),
						kind=PathKind.FILE,
					)
				)

		return tuple(checks)
