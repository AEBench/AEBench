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
	try:
		manifest = json.loads(
			(context.case_dir / "refs" / "dataset_manifest.json").read_text(encoding="utf-8")
		)
	except OSError as exc:
		raise ValueError(f"failed to read dataset manifest: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid dataset manifest: {exc}") from exc

	dataset_root_value = manifest.get("dataset_root")
	subdirs = manifest.get("subdirs")
	basenames = manifest.get("basenames")
	if not isinstance(dataset_root_value, str) or not dataset_root_value.strip():
		raise ValueError("dataset manifest has invalid dataset_root")
	if not isinstance(subdirs, list) or not all(
		isinstance(value, str) and value.strip() for value in subdirs
	):
		raise ValueError("dataset manifest has invalid subdirs")
	if not isinstance(basenames, list) or not all(
		isinstance(value, str) and value.strip() for value in basenames
	):
		raise ValueError("dataset manifest has invalid basenames")

	dataset_root = repo_root / dataset_root_value
	expected_basenames = {value.strip() for value in basenames}
	reqs: list[Checkable] = [
		FilesystemPathCheck(
			name="dataset_root_exists",
			path=dataset_root,
			path_type=PathType.DIRECTORY,
		),
	]
	for subdir in subdirs:
		reqs.append(
			FilesystemPathCheck(
				name=f"dataset_subdir_exists_{subdir}",
				path=dataset_root / subdir,
				path_type=PathType.DIRECTORY,
			)
		)

	def _check_basenames() -> utils.CheckResult:
		missing: list[str] = []
		for subdir in subdirs:
			subdir_path = dataset_root / subdir
			if not subdir_path.is_dir():
				continue
			try:
				present = {
					path.stem
					for path in subdir_path.iterdir()
					if path.is_file() and not path.name.startswith(".")
				}
			except OSError as exc:
				return utils.CheckResult.failure(
					f"failed to read dataset directory {subdir_path}: {exc}"
				)
			absent = sorted(expected_basenames - present)
			if absent:
				missing.append(f"{subdir_path}: missing {', '.join(absent[:5])}")
		if missing:
			return utils.CheckResult.failure("; ".join(missing))
		return utils.CheckResult.success("dataset basenames match the manifest")

	reqs.append(
		utils.Check(
			name="dataset_basenames_present",
			fn=_check_basenames,
		)
	)
	return tuple(reqs)
