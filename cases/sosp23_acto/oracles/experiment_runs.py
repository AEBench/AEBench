from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, PathKind, utils

from . import custom

TableParser = Callable[[str], custom.TableData]

_TABLES: tuple[tuple[str, TableParser], ...] = (
	("table5", custom.parse_table5),
	("table6", custom.parse_table6),
	("table7", custom.parse_table7),
	("table8", custom.parse_table8),
)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		requirements: list[utils.BaseCheck] = []

		for table_name, parser in _TABLES:
			observed_path = self.workspace_path(f"{table_name}.txt")
			reference_path = self.ref_path(f"{table_name}.ref.json")
			requirements.extend(
				(
					self.path_check(
						name=f"{table_name}_result_exists",
						path=observed_path,
						kind=PathKind.FILE,
					),
					self.path_check(
						name=f"{table_name}_reference_exists",
						path=reference_path,
						kind=PathKind.FILE,
					),
					utils.Check(
						name=f"{table_name}_matches_reference",
						fn=partial(
							self._check_table,
							table_name=table_name,
							observed_path=observed_path,
							reference_path=reference_path,
							parser=parser,
						),
					),
				)
			)

		return tuple(requirements)

	def _check_table(
		self,
		*,
		table_name: str,
		observed_path: Path,
		reference_path: Path,
		parser: TableParser,
	) -> utils.CheckResult:
		try:
			observed = parser(self.read_text(observed_path))
			reference = json.loads(self.read_text(reference_path))
		except (OSError, RuntimeError, ValueError) as exc:
			return utils.CheckResult.failure(f"failed to load {table_name}: {exc}")

		if not isinstance(reference, dict):
			return utils.CheckResult.failure(f"{table_name} reference must contain a JSON object")
		if observed == reference:
			return utils.CheckResult.success(f"{table_name} matches the reference")

		expected_preview = json.dumps(reference, sort_keys=True)[:2000]
		observed_preview = json.dumps(observed, sort_keys=True)[:2000]
		return utils.CheckResult.failure(
			f"{table_name} differs from the reference\n"
			f"expected={expected_preview}\nobserved={observed_preview}"
		)
