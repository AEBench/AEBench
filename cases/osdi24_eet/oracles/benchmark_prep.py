from __future__ import annotations

import json
from collections.abc import Sequence

from evaluator.oracles.benchmark_prep_checks import BenchmarkCommandCheck
from evaluator.oracles.discovery import benchmark_prep
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.utils import Checkable
from models import OracleInput


@benchmark_prep
def oracle_benchmark_prep(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	manifest_path = context.case_dir / "refs" / "benchmark_manifest.json"
	try:
		manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
	except OSError as exc:
		raise ValueError(f"failed to read benchmark manifest: {exc}") from exc
	except json.JSONDecodeError as exc:
		raise ValueError(f"invalid benchmark manifest: {exc}") from exc

	benchmarks = manifest.get("benchmarks")
	required_files = manifest.get("required_files")
	versions = manifest.get("versions")
	if not isinstance(benchmarks, list) or not all(
		isinstance(value, str) and value.strip() for value in benchmarks
	):
		raise ValueError("benchmark manifest has invalid benchmarks")
	if not isinstance(required_files, list) or not all(
		isinstance(value, str) and value.strip() for value in required_files
	):
		raise ValueError("benchmark manifest has invalid required_files")
	if not isinstance(versions, dict) or not all(
		isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
		for key, value in versions.items()
	):
		raise ValueError("benchmark manifest has invalid versions")

	reqs: list[Checkable] = [
		FilesystemPathCheck(
			name="repo_root_exists",
			path=repo_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="scripts_dir_exists",
			path=repo_root / "scripts",
			path_type=PathType.DIRECTORY,
		),
	]
	for benchmark in benchmarks:
		benchmark_dir = repo_root / "scripts" / benchmark
		reqs.append(
			FilesystemPathCheck(
				name=f"scripts_subdir_{benchmark}",
				path=benchmark_dir,
				path_type=PathType.DIRECTORY,
			)
		)
		for filename in required_files:
			reqs.append(
				FilesystemPathCheck(
					name=f"scripts_file_{benchmark}_{filename}",
					path=benchmark_dir / filename,
					path_type=PathType.FILE,
				)
			)
		version = versions.get(benchmark)
		if not isinstance(version, str) or not version.strip():
			raise ValueError(f"benchmark manifest is missing version for {benchmark!r}")
		reqs.append(
			BenchmarkCommandCheck(
				name=f"run_test_contains_version_{benchmark}",
				cwd=benchmark_dir,
				cmd=("cat", "run_test.sh"),
				signature=version,
				timeout_seconds=10.0,
			)
		)
	return tuple(reqs)
