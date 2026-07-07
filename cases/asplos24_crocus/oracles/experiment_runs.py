from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import CaseOracleExperimentRunsBase
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	CASE_STUDY_SIGNATURES,
	CDF_PDF_GLOB,
	COVERAGE_PCT_EPSILON,
	COVERAGE_REF,
	COVERAGE_RUSTC_FILE,
	COVERAGE_WASMTIME_FILE,
	RESULTS_DIR,
	TABLE1_FILE,
	TABLE1_REF,
	TABLE1_SUCCESS_TOLERANCE,
)
from .parsing import CoveragePercentCheck, FileContainsCheck, Table1CountsCheck


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the agent-saved experiment outputs under crocus-results/."""

	def requirements(self) -> Sequence[BaseCheck]:
		table1 = json.loads(self.ref_path(TABLE1_REF).read_text(encoding="utf-8"))
		coverage = json.loads(self.ref_path(COVERAGE_REF).read_text(encoding="utf-8"))

		checks: list[BaseCheck] = []

		# Table 1 verification counts.
		checks.append(
			Table1CountsCheck(
				name="table1_counts",
				path=self.artifact_path(RESULTS_DIR, TABLE1_FILE),
				rules_total=table1["rules_total"],
				rules_success_all=table1["rules_success_all"],
				rules_success_any=table1["rules_success_any"],
				rules_failure=table1["rules_failure"],
				type_insts_total=table1["type_insts_total"],
				type_insts_success=table1["type_insts_success"],
				type_insts_inapplicable=table1["type_insts_inapplicable"],
				type_insts_failure=table1["type_insts_failure"],
				success_tolerance=TABLE1_SUCCESS_TOLERANCE,
			)
		)

		# Six case-study reproductions (signature lines; hex/bit values ignored).
		for filename, signatures in CASE_STUDY_SIGNATURES.items():
			checks.append(
				FileContainsCheck(
					name=f"case_study_{filename.removesuffix('.txt')}",
					path=self.artifact_path(RESULTS_DIR, filename),
					required=signatures,
				)
			)

		# Figure 4 CDF: file-existence only (no numeric claim). The PDF name is
		# timestamped, so match by glob.
		checks.append(
			self.min_matching_entry_count_check(
				name="cdf_pdf_exists",
				directory=self.artifact_path(RESULTS_DIR),
				pattern=CDF_PDF_GLOB,
				min_count=1,
			)
		)

		# Part 3 rule-coverage percentages (deterministic on the pre-saved CSVs).
		checks.append(
			CoveragePercentCheck(
				name="coverage_wasmtime",
				path=self.artifact_path(RESULTS_DIR, COVERAGE_WASMTIME_FILE),
				expected_uses_pct=coverage["wasmtime"]["uses_pct"],
				expected_covered_pct=coverage["wasmtime"]["covered_pct"],
				epsilon=COVERAGE_PCT_EPSILON,
			)
		)
		checks.append(
			CoveragePercentCheck(
				name="coverage_rustc",
				path=self.artifact_path(RESULTS_DIR, COVERAGE_RUSTC_FILE),
				expected_uses_pct=coverage["rustc"]["uses_pct"],
				expected_covered_pct=coverage["rustc"]["covered_pct"],
				epsilon=COVERAGE_PCT_EPSILON,
			)
		)

		return tuple(checks)
