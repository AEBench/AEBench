"""Workspace and case registry helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from constants import CASE_MANIFEST_FILENAME
from models import UpstreamConfig, UpstreamSourceType
from project_config import (
	BundleRegistry,
	BundleRegistryCase,
	ProjectState,
	default_user_config_path,
	load_bundle_registry_file,
)

_WORKSPACE_CONFIG_TEXT = "# AEBench workspace configuration.\n"


@dataclass(frozen=True, slots=True)
class WorkspaceInitResult:
	created: tuple[Path, ...]


def initialize_workspace(workspace: Path) -> WorkspaceInitResult:
	root = workspace.expanduser().resolve()
	root.mkdir(parents=True, exist_ok=True)

	created: list[Path] = []

	config_path = root / "aebench.toml"
	if not config_path.exists():
		config_path.write_text(_WORKSPACE_CONFIG_TEXT, encoding="utf-8")
		created.append(config_path)

	registry_path = root / "cases.json"
	if not registry_path.exists():
		_write_registry(registry_path, _empty_registry())
		created.append(registry_path)

	cases_dir = root / "cases"
	if not cases_dir.exists():
		cases_dir.mkdir(parents=True, exist_ok=True)
		created.append(cases_dir)

	return WorkspaceInitResult(created=tuple(created))


def initialize_user_config() -> Path:
	path = default_user_config_path()
	path.parent.mkdir(parents=True, exist_ok=True)
	if not path.exists():
		path.write_text("", encoding="utf-8")
	return path


def register_case(
	case_id: str,
	case_dir: Path,
	*,
	project_state: ProjectState,
) -> None:
	registry_path = project_state.root / "cases.json"
	registry = (
		load_bundle_registry_file(registry_path) if registry_path.exists() else _empty_registry()
	)

	registry.cases[case_id] = BundleRegistryCase(
		path=_relative_to_project_root(case_dir, project_state.root)
	)
	_write_registry(registry_path, registry)


class CaseResolutionError(ValueError):
	"""A case reference names no existing case bundle."""


def resolve_case_ref(case_ref: str, *, project_state: ProjectState) -> Path:
	"""Resolves a user case reference to an existing case directory.

	Tries, in order: a path relative to the current directory (or absolute), a
	registered id via its cases.json path, a path relative to the project root,
	and an unregistered id under the cases dir.

	Raises:
		CaseResolutionError: If no candidate contains a case manifest.
	"""
	root = project_state.root
	entry = project_state.registry.cases.get(case_ref)
	candidates = [
		Path(case_ref).expanduser(),
		*([entry.resolve(root)] if entry is not None else []),
		root / case_ref,
		project_state.config.resolve_bundles_dir(root) / case_ref,
	]
	for candidate in candidates:
		if (candidate / CASE_MANIFEST_FILENAME).is_file():
			return candidate.resolve()
	if entry is not None:
		raise CaseResolutionError(
			f"cannot resolve case reference: {case_ref} "
			f"(registered at {entry.path}, which has no {CASE_MANIFEST_FILENAME})"
		)
	raise CaseResolutionError(f"cannot resolve case reference: {case_ref}")


def new_case_dir(
	case_id: str, *, project_state: ProjectState, target_dir: Path | None = None
) -> Path:
	"""Returns where `case init` creates a case: target_dir, else the cases dir."""
	if target_dir is not None:
		return target_dir.expanduser().resolve()
	return project_state.config.resolve_bundles_dir(project_state.root) / case_id


def infer_upstream_config(source: str, ref: str | None = None) -> UpstreamConfig:
	path = Path(source).expanduser()
	if path.exists():
		return UpstreamConfig(
			source_type=UpstreamSourceType.LOCAL,
			path=str(path.resolve()),
			requested_ref=ref,
		)

	parsed = urlparse(source)
	if source.startswith("git@") or source.endswith(".git") or parsed.scheme in {"ssh", "git"}:
		return UpstreamConfig(
			source_type=UpstreamSourceType.GIT,
			url=source,
			ref=ref,
			requested_ref=ref,
		)

	if parsed.scheme in {"http", "https"}:
		return UpstreamConfig(
			source_type=UpstreamSourceType.ARCHIVE,
			url=source,
			requested_ref=ref,
		)

	raise ValueError(f"unsupported case source: {source}")


def _empty_registry() -> BundleRegistry:
	return BundleRegistry.model_validate(
		{
			"cases_dir": "cases",
			"default_source": {
				"url": None,
				"ref": None,
				"bundles_subdir": "cases",
			},
			"cases": {},
		}
	)


def _relative_to_project_root(path: Path, root: Path) -> str:
	try:
		return path.expanduser().resolve().relative_to(root.expanduser().resolve()).as_posix()
	except ValueError as exc:
		raise ValueError(f"case bundle must live under workspace root: {path}") from exc


def _write_registry(path: Path, registry: BundleRegistry) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(
		json.dumps(registry.model_dump(mode="json", by_alias=True), indent=2) + "\n",
		encoding="utf-8",
	)


__all__ = [
	"CaseResolutionError",
	"WorkspaceInitResult",
	"infer_upstream_config",
	"initialize_user_config",
	"initialize_workspace",
	"new_case_dir",
	"register_case",
	"resolve_case_ref",
]
