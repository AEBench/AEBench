from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind
from evaluator.oracles.reporting import BaseCheck


def _load_benchmark_manifest(path: Path) -> tuple[list[str], list[str], dict[str, str]]:
	try:
		manifest = json.loads(path.read_text(encoding="utf-8"))
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

	return benchmarks, required_files, versions


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()
		manifest_path = self.ref_path("benchmark_manifest.json")

		benchmarks, required_files, versions = _load_benchmark_manifest(manifest_path)

		reqs: list[BaseCheck] = [
			self.path_check(
				name="repo_root_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="scripts_dir_exists",
				path=repo_root / "scripts",
				kind=PathKind.DIRECTORY,
			),
		]

		for benchmark in benchmarks:
			benchmark_dir = repo_root / "scripts" / benchmark

			reqs.append(
				self.path_check(
					name=f"scripts_subdir_{benchmark}",
					path=benchmark_dir,
					kind=PathKind.DIRECTORY,
				)
			)

			for filename in required_files:
				reqs.append(
					self.path_check(
						name=f"scripts_file_{benchmark}_{filename}",
						path=benchmark_dir / filename,
						kind=PathKind.FILE,
					)
				)

			version = versions.get(benchmark)
			if not isinstance(version, str) or not version.strip():
				raise ValueError(f"benchmark manifest is missing version for {benchmark!r}")

			reqs.append(
				self.command_check(
					name=f"run_test_contains_version_{benchmark}",
					cwd=benchmark_dir,
					cmd=("cat", "run_test.sh"),
					signature=version,
					timeout_seconds=10.0,
				)
			)

		return tuple(reqs)
