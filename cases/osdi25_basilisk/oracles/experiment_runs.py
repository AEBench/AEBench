from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import PROTOCOLS
from .parsing import HintsCsvCheck, SlocCsvCheck, VerificationLogCheck

_VERIFY_LOG = "basilisk/verify-all.log"
_HINTS_CSV = "basilisk/evaluation/hints.csv"
_SLOC_CSV = "basilisk/evaluation/sloc.csv"
_RESULTS_REF = "basilisk_results.ref.json"
_OWNERSHIP_PROTOCOLS = frozenset({"distributedLock", "shardedKv", "shardedKvBatched", "lockServer"})


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate full verification coverage and both paper-evaluation tables."""

	def requirements(self) -> Sequence[BaseCheck]:
		executor = self.executor
		ref_path = self.ref_path(_RESULTS_REF)
		checks: list[BaseCheck] = [
			VerificationLogCheck(
				name="all_protocols_verify",
				path=self.runtime_path(_VERIFY_LOG),
				executor=executor,
			),
			HintsCsvCheck(
				name="hint_counts_match",
				path=self.runtime_path(_HINTS_CSV),
				reference_path=ref_path,
				executor=executor,
			),
			SlocCsvCheck(
				name="sloc_results_match",
				path=self.runtime_path(_SLOC_CSV),
				reference_path=ref_path,
				executor=executor,
			),
		]

		for protocol in PROTOCOLS:
			safe_name = protocol.replace("-", "_")
			generated_dir = f"basilisk/{protocol}/automate_gen2"
			generated_files = [
				("footprints", "footprintsAutogen.json"),
				("message_invariants", "messageInvariantsAutogen.dfy"),
				("monotonicity_invariants", "monotonicityInvariantsAutogen.dfy"),
			]
			if protocol in _OWNERSHIP_PROTOCOLS:
				generated_files.append(("ownership_invariants", "ownershipInvariantsAutogen.dfy"))

			for label, filename in generated_files:
				checks.append(
					self.path_check(
						name=f"{safe_name}_{label}_generated",
						path=self.runtime_path(generated_dir, filename),
						kind=PathKind.FILE,
					)
				)

		return tuple(checks)
