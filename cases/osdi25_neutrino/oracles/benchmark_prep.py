from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Sequence
from typing import Any

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType


def _load_json_object(path: pathlib.Path) -> dict[str, Any]:
	value = json.loads(path.read_text(encoding="utf-8"))
	if not isinstance(value, dict):
		raise ValueError(f"expected JSON object, got {type(value).__name__}")
	return value


def _manifest_string_list(
	manifest: dict[str, Any],
	key: str,
	*,
	check_name: str,
) -> list[str]:
	value = manifest.get(key)
	if not isinstance(value, list) or not all(
		isinstance(item, str) and item.strip() for item in value
	):
		raise ValueError(f"{check_name}: benchmark manifest missing valid {key}")
	return [item.strip() for item in value]


def _resolve_relative_path(
	base_dir: pathlib.Path,
	relative_path: str,
	*,
	check_name: str,
) -> pathlib.Path:
	rel = pathlib.Path(relative_path)
	if rel.is_absolute():
		raise ValueError(f"{check_name}: manifest path must be relative: {relative_path!r}")
	if any(part == ".." for part in rel.parts):
		raise ValueError(f"{check_name}: manifest path escapes base directory: {relative_path!r}")
	return base_dir / rel


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkRequiredFilesCheck(utils.BaseCheck):
	manifest_path: pathlib.Path
	workspace_dir: pathlib.Path

	def __post_init__(self) -> None:
		object.__setattr__(self, "manifest_path", pathlib.Path(self.manifest_path))
		object.__setattr__(self, "workspace_dir", pathlib.Path(self.workspace_dir))

	def check(self) -> utils.CheckResult:
		try:
			manifest = _load_json_object(self.manifest_path)
			required_files = _manifest_string_list(
				manifest,
				"required_files",
				check_name=self.name,
			)
		except (OSError, json.JSONDecodeError, ValueError) as exc:
			return utils.CheckResult.failure(f"benchmark manifest unreadable or invalid: {exc}")

		missing: list[str] = []
		for relative_path in required_files:
			try:
				path = _resolve_relative_path(
					self.workspace_dir,
					relative_path,
					check_name=self.name,
				)
			except ValueError as exc:
				return utils.CheckResult.failure(str(exc))

			if not path.exists():
				missing.append(str(path))

		if missing:
			return utils.CheckResult.failure(
				"benchmark source files are missing: " + "; ".join(missing)
			)

		return utils.CheckResult.success()


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		manifest_path = self.ref_path("benchmark_manifest.json")

		return (
			FilesystemPathCheck(
				name="benchmark_manifest_exists",
				path=manifest_path,
				path_type=PathType.FILE,
			),
			BenchmarkRequiredFilesCheck(
				name="required_benchmark_files_exist",
				manifest_path=manifest_path,
				workspace_dir=self.paths.workspace_dir,
			),
		)