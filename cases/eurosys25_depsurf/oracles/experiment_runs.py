from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	CONFIG_DIFF_PAIRS,
	FIGURE_FILES,
	FIGURES_DIR,
	OUTPUT_DIR,
	PKL_FILES,
	SRC_DIFF_PAIRS,
	TABLE_FILES,
	TABLE_MIN_MATCH_FRACTION,
	TABLES_DIR,
	TABLES_REF,
	VERSION_DUMPS,
)
from .parsing import (
	ConfigTableProvenanceCheck,
	LatexTableCheck,
	ProgramReportsCheck,
	SrcTableProvenanceCheck,
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	"""Validate the tables, figures, and intermediate data the notebooks write."""

	def requirements(self) -> Sequence[BaseCheck]:
		refs = json.loads(self.read_text(self.ref_path(TABLES_REF)))
		tables = self.workspace_path(*TABLES_DIR)
		output = self.workspace_path(*OUTPUT_DIR)

		checks: list[BaseCheck] = []

		# Intermediate data: the pickles the table and figure cells load, and the
		# per-version dumps (only counted; output/inline alone is close to 1 GB).
		for filename in PKL_FILES:
			checks.append(
				self.path_check(
					name=f"pickle_{filename.removesuffix('.pkl')}",
					path=output / filename,
					kind=PathKind.FILE,
				)
			)
		for directory, (filename, count) in VERSION_DUMPS.items():
			checks.append(
				self.min_matching_entry_count_check(
					name=f"{directory}_version_dumps",
					directory=output / directory,
					pattern=f"*/{filename}",
					min_count=count,
				)
			)

		# Figures: existence and a PDF header only, as their data is in the pickles.
		for name, filename in FIGURE_FILES.items():
			checks.append(
				self.command_check(
					name=f"{name}_pdf",
					cmd=["head", "-c", "5", "/".join((*FIGURES_DIR, filename))],
					cwd=self.workspace_path(),
					timeout_seconds=10.0,
					signature="%PDF-",
				)
			)

		# Tables 3-8: cells against the authors' saved notebook outputs.
		for name, filename in TABLE_FILES.items():
			table = refs["tables"][filename]
			checks.append(
				LatexTableCheck(
					name=name,
					path=tables / filename,
					rows=table["rows"],
					key_columns=table["key_columns"],
					min_match_fraction=TABLE_MIN_MATCH_FRACTION,
				)
			)

		# Provenance: the tables must agree with this run's intermediate data, so
		# copying the reference values into paper/tabs is not enough.
		checks.append(
			SrcTableProvenanceCheck(
				name="table3_matches_src_diffs",
				path=tables / TABLE_FILES["table3_src"],
				groups={
					label: [(pair, output / "src" / group / pair / "Summary.txt") for pair in pairs]
					for label, (group, pairs) in SRC_DIFF_PAIRS.items()
				},
			)
		)
		checks.append(
			ConfigTableProvenanceCheck(
				name="table5_matches_config_diffs",
				path=tables / TABLE_FILES["table5_config"],
				summaries=[output / "config" / pair / "Summary.txt" for pair in CONFIG_DIFF_PAIRS],
			)
		)
		checks.append(
			ProgramReportsCheck(
				name="program_reports",
				report_dir=output / "report",
				programs=refs["programs"],
				table_path=tables / TABLE_FILES["table7_bcc"],
			)
		)

		return checks
