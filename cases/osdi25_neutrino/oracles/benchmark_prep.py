from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.discovery import benchmark_prep
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.utils import Checkable
from models import OracleInput


@benchmark_prep
def oracle_benchmark_prep(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	manifest_path = context.case_dir / "refs" / "benchmark_manifest.json"

	def _validate_required_files() -> utils.CheckResult:
		try:
			manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			return utils.CheckResult.failure(f"benchmark manifest unreadable: {exc}")

		required_files = manifest.get("required_files")
		if not isinstance(required_files, list) or not all(
			isinstance(value, str) and value.strip() for value in required_files
		):
			return utils.CheckResult.failure("benchmark manifest missing valid required_files")

		missing = [
			str(repo_root / relative_path)
			for relative_path in required_files
			if not (repo_root / relative_path).exists()
		]
		if missing:
			return utils.CheckResult.failure(
				"benchmark source files are missing: " + "; ".join(missing)
			)
		return utils.CheckResult.success()

	return (
		FilesystemPathCheck(
			name="benchmark_manifest_exists",
			path=manifest_path,
			path_type=PathType.FILE,
		),
		utils.Check(
			name="required_benchmark_files_exist",
			fn=_validate_required_files,
		),
	)
