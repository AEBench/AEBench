from __future__ import annotations

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
	manifest_path = context.case_dir / "refs" / "benchmark_manifest.json"
	results_root = repo_root / "results"

	def _validate_results() -> utils.CheckResult:
		try:
			manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			return utils.CheckResult.failure(f"benchmark manifest unreadable: {exc}")

		result_files = manifest.get("result_files")
		if not isinstance(result_files, list) or not all(
			isinstance(value, str) and value.strip() for value in result_files
		):
			return utils.CheckResult.failure("benchmark manifest missing valid result_files")

		missing: list[str] = []
		for relative_path in result_files:
			path = results_root / relative_path
			if not path.is_file():
				missing.append(str(path))
				continue
			if path.suffix == ".json":
				try:
					json.loads(path.read_text(encoding="utf-8"))
				except (OSError, json.JSONDecodeError) as exc:
					return utils.CheckResult.failure(f"invalid JSON result file {path}: {exc}")

		if missing:
			return utils.CheckResult.failure(
				"expected benchmark result files are missing: " + "; ".join(missing)
			)
		return utils.CheckResult.success()

	return (
		FilesystemPathCheck(
			name="results_root_exists",
			path=results_root,
			path_type=PathType.DIRECTORY,
		),
		utils.Check(
			name="required_result_files_present",
			fn=_validate_results,
		),
	)
