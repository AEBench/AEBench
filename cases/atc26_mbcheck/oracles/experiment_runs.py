from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .checks import (
	LitmusOnDiskCheck,
	MicrobenchmarkCheck,
	RankedCountsCheck,
	RuntimeBreakdownCheck,
	SweepCountsCheck,
	SweepProvenanceCheck,
	ViolationsByModuleCheck,
)
from .consts import CORPUS_DIR, RESULTS_REF, SWEEP_JSON, SWEEP_LITMUS_DIR, SYNC_CASES, TABLE8_JSON


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the v6.19 sweep behind Tables 4-7 and 9, and the Table 8 suite."""

	def requirements(self) -> Sequence[BaseCheck]:
		sweep = self.runtime_path(SWEEP_JSON)
		ref = self.ref_path(RESULTS_REF)
		return (
			SweepProvenanceCheck(
				name="sweep_covers_v619_corpus",
				sweep_path=sweep,
				reference_path=ref,
				corpus_dir=CORPUS_DIR,
			),
			LitmusOnDiskCheck(
				name="sweep_litmus_tests_on_disk",
				sweep_path=sweep,
				litmus_dir=SWEEP_LITMUS_DIR,
			),
			SweepCountsCheck(
				name="table4_kernel_sweep",
				sweep_path=sweep,
				reference_path=ref,
				section="table4",
			),
			RuntimeBreakdownCheck(
				name="table5_runtime_breakdown",
				sweep_path=sweep,
				reference_path=ref,
			),
			RankedCountsCheck(
				name="table6_top_anchors",
				sweep_path=sweep,
				reference_path=ref,
				sweep_key="anchor_labels",
				ref_keys=("table6", "anchors"),
			),
			RankedCountsCheck(
				name="table6_top_external_functions",
				sweep_path=sweep,
				reference_path=ref,
				sweep_key="anchor_labels",
				ref_keys=("table6", "external_functions"),
			),
			RankedCountsCheck(
				name="table7_top_heuristics",
				sweep_path=sweep,
				reference_path=ref,
				sweep_key="heuristics",
				ref_keys=("table7",),
			),
			MicrobenchmarkCheck(
				name="table8_microbenchmark_all_tn",
				table8_path=self.runtime_path(TABLE8_JSON),
				reference_path=ref,
				cases=SYNC_CASES,
			),
			ViolationsByModuleCheck(
				name="table9_violations_by_module",
				sweep_path=sweep,
				reference_path=ref,
			),
		)
