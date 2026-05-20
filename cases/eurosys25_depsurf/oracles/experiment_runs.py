from __future__ import annotations

import csv
import json
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.discovery import experiment_runs
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.utils import Checkable
from models import OracleInput


@experiment_runs
def oracle_experiment_runs(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	try:
		manifest = json.loads(
			(context.case_dir / "refs" / "results_manifest.json").read_text(encoding="utf-8")
		)
	except OSError as exc:
		raise ValueError(f"failed to read results manifest: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid results manifest: {exc}") from exc

	result_files = manifest.get("result_files")
	if not isinstance(result_files, list) or not all(
		isinstance(value, str) and value.strip() for value in result_files
	):
		raise ValueError("results manifest has invalid result_files")

	results_root = repo_root / "results"
	reqs: list[Checkable] = [
		FilesystemPathCheck(
			name="results_root_exists",
			path=results_root,
			path_type=PathType.DIRECTORY,
		),
	]

	for relative_path in result_files:
		result_path = results_root / relative_path

		def _make_result_check(path: str) -> utils.Check:
			def _check() -> utils.CheckResult:
				csv_path = results_root / path
				if not csv_path.is_file():
					return utils.CheckResult.failure(f"missing result file: {csv_path}")
				try:
					with csv_path.open("r", encoding="utf-8", newline="") as handle:
						rows = [
							row for row in csv.reader(handle) if any(cell.strip() for cell in row)
						]
				except OSError as exc:
					return utils.CheckResult.failure(
						f"failed to read result file {csv_path}: {exc}"
					)
				except csv.Error as exc:
					return utils.CheckResult.failure(f"invalid CSV in {csv_path}: {exc}")

				if len(rows) < 2:
					return utils.CheckResult.failure(
						f"expected at least one data row in {csv_path}"
					)
				return utils.CheckResult.success(f"parsed {csv_path}")

			safe_name = path.replace("/", "_").replace(".", "_")
			return utils.Check(name=f"result_file_parseable_{safe_name}", fn=_check)

		reqs.append(
			FilesystemPathCheck(
				name=f"result_file_exists_{relative_path.replace('/', '_').replace('.', '_')}",
				path=result_path,
				path_type=PathType.FILE,
			)
		)
		reqs.append(_make_result_check(relative_path))
	return tuple(reqs)
