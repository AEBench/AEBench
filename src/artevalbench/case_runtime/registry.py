from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..models import PromptProfile, RuntimeMode
from ..project_config import (
 BundleRegistry,
 BundleRegistryCase,
 BundleSourceConfig,
 ProjectConfigState,
 default_user_config_path,
 load_bundle_registry_file,
 load_project_config,
)
from ..utils import safe_name
from .manifest import write_case_spec
from .models import CaseSpec
from .oracle_templates import render_oracle_template_set

_DEFAULT_BUNDLES_DIR = "bundles"


@dataclass(frozen=True)
class WorkspaceInitResult:
	created: list[Path] = field(default_factory=list)
	updated: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class UserInitResult:
	config_path: Path
	created: list[Path] = field(default_factory=list)
	updated: list[Path] = field(default_factory=list)


def initialize_user_config(*, force: bool = False) -> UserInitResult:
	config_path = default_user_config_path().resolve()
	created: list[Path] = []
	updated: list[Path] = []

	config_root = config_path.parent
	config_root.mkdir(parents=True, exist_ok=True)
	cache_root = Path("~/.cache/artevalbench/git").expanduser().resolve()
	case_runs_root = Path("~/.cache/artevalbench/case-runs").expanduser().resolve()
	for directory in [cache_root, case_runs_root]:
		if not directory.exists():
			directory.mkdir(parents=True, exist_ok=True)
			created.append(directory.resolve())
		else:
			directory.mkdir(parents=True, exist_ok=True)
	_write_if_needed(config_path, _default_user_toml(), force, created, updated)
	return UserInitResult(config_path=config_path, created=created, updated=updated)


def initialize_workspace(
 root: Path,
 *,
 force: bool = False,
 include_local_config: bool = False,
) -> WorkspaceInitResult:
	workspace_root = root.resolve()
	registry_path = workspace_root / "bundles.json"
	registry = (
	 load_bundle_registry_file(registry_path)
	 if registry_path.is_file() and not force
	 else default_bundle_registry(workspace_root)
	)
	created: list[Path] = []
	updated: list[Path] = []

	_write_if_needed(registry_path, bundle_registry_to_json(registry), force, created, updated)
	if include_local_config:
		_write_if_needed(
		 workspace_root / "artevalbench.toml",
		 _default_workspace_toml(),
		 force,
		 created,
		 updated,
		)

	bundles_root = workspace_root / registry.bundles_dir
	bundles_root.mkdir(parents=True, exist_ok=True)
	return WorkspaceInitResult(created=created, updated=updated)


def initialize_case_bundle(
 case_id: str,
 *,
 project_state: ProjectConfigState,
 target_root: Path | None = None,
) -> Path:
	initialize_workspace(project_state.root, include_local_config=False)
	project_state = load_project_config(project_state.root, config_path=project_state.path)
	registry_path = project_state.root / "bundles.json"
	registry = load_bundle_registry_file(registry_path)
	bundles_root = (target_root or (project_state.root / registry.bundles_dir)).resolve()
	if not bundles_root.is_relative_to(project_state.root):
		raise RuntimeError("case bundles must live under the workspace root to be registered")
	bundles_root.mkdir(parents=True, exist_ok=True)
	(bundles_root / "__init__.py").touch(exist_ok=True)
	bundle_dir = bundles_root / safe_name(case_id)
	if bundle_dir.exists():
		raise RuntimeError(f"bundle already exists: {bundle_dir}")
	bundle_dir.mkdir(parents=True, exist_ok=False)
	(bundle_dir / "__init__.py").write_text(
	 '"""Bundle content package for the case."""\n', encoding="utf-8"
	)
	oracle_dir = bundle_dir / "oracle"
	refs_dir = bundle_dir / "refs"
	artifact_dir = bundle_dir / "artifact"
	oracle_dir.mkdir(parents=True, exist_ok=False)
	refs_dir.mkdir(parents=True, exist_ok=False)
	artifact_dir.mkdir(parents=True, exist_ok=False)
	(oracle_dir / "__init__.py").write_text(
	 '"""Oracle package for the case bundle."""\n', encoding="utf-8"
	)
	write_placeholder_oracle_package(oracle_dir, case_id)
	(refs_dir / ".gitkeep").write_text("", encoding="utf-8")
	(artifact_dir / "README.md").write_text(
	 "# Instructions\n\nReplace this file with the artifact reproduction instructions.\n",
	 encoding="utf-8",
	)

	case = CaseSpec.model_validate(
	 {
	  "id": case_id,
	  "case_card": _todo_case_card(case_id),
	  "run": {
	   "id": case_id,
	   "instructions": {"path": "README.md"},
	   "runtime": {"mode": RuntimeMode.DOCKER.value},
	   "prompt": {"profile": PromptProfile.ARTIFACT_EVAL_V1.value},
	  },
	  "oracle": {
	   "placeholder": True,
	   "notes": "Replace the placeholder oracle package with case-specific decorated phases and refs.",
	  },
	  "upstream": {"source_type": "vendored"},
	 }
	)
	write_case_spec(bundle_dir / "case.toml", case)
	register_case_bundle(case.id, bundle_dir, project_state=project_state)
	return bundle_dir


def register_case_bundle(
 case_id: str,
 case_dir: Path,
 *,
 project_state: ProjectConfigState,
) -> Path:
	case_root = case_dir.resolve()
	workspace_root = project_state.root.resolve()
	if not case_root.is_relative_to(workspace_root):
		raise RuntimeError("registered case bundles must live under the workspace root")
	registry_path = project_state.registry_path or (workspace_root / "bundles.json")
	registry = (
	 load_bundle_registry_file(registry_path)
	 if registry_path.is_file()
	 else default_bundle_registry(workspace_root)
	)
	registry.cases[case_id] = BundleRegistryCase(
	 path=case_root.relative_to(workspace_root).as_posix()
	)
	write_bundle_registry(registry_path, registry)
	return registry_path


def bundle_registry_to_json(registry: BundleRegistry) -> str:
	payload = registry.model_dump(mode="json")
	cases = payload.get("cases", {})
	if isinstance(cases, dict):
		payload["cases"] = {case_id: cases[case_id] for case_id in sorted(cases)}
	return json.dumps(payload, indent=2) + "\n"


def write_bundle_registry(path: Path, registry: BundleRegistry) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(bundle_registry_to_json(registry), encoding="utf-8")


def default_bundle_registry(root: Path) -> BundleRegistry:
	return BundleRegistry(
	 bundles_dir=_DEFAULT_BUNDLES_DIR,
	 default_source=BundleSourceConfig(
	  url=_git_remote_url(root),
	  ref=None,
	  bundles_subdir=_DEFAULT_BUNDLES_DIR,
	 ),
	)


def _default_workspace_toml() -> str:
	return 'artifact_mode = "vendor"\n'


def _default_user_toml() -> str:
	return (
	 "# Global ArtEvalBench defaults.\n"
	 "# Edit this file to change cache, case run, logging, or agent defaults.\n\n"
	 'case_runs_dir = "~/.cache/artevalbench/case-runs"\n\n'
	 "[cache.git]\n"
	 'root = "~/.cache/artevalbench/git"\n'
	)


def _write_if_needed(
 path: Path,
 content: str,
 force: bool,
 created: list[Path],
 updated: list[Path],
) -> None:
	if path.exists():
		if not force:
			return
		path.write_text(content, encoding="utf-8")
		updated.append(path.resolve())
		return
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding="utf-8")
	created.append(path.resolve())


def _git_remote_url(root: Path) -> str | None:
	if not (root / ".git").exists():
		return None
	result = subprocess.run(
	 ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
	 capture_output=True,
	 text=True,
	 timeout=30,
	 check=False,
	)
	value = result.stdout.strip()
	return value or None


def render_placeholder_oracle(case_id: str) -> str:
	return render_placeholder_oracle_files(case_id)["custom.py"]


def write_placeholder_oracle_package(oracle_dir: Path, case_id: str) -> None:
	for relative_path, content in render_placeholder_oracle_files(case_id).items():
		(oracle_dir / relative_path).write_text(content, encoding="utf-8")


def render_placeholder_oracle_files(case_id: str) -> dict[str, str]:
	return render_oracle_template_set(
	 "placeholder",
	 replacements={"__CASE_ID__": case_id},
	)


def _todo_case_card(case_id: str) -> dict[str, str]:
	return {
	 "core_claim": f"TODO: summarize the core clean-baseline claim for {case_id}.",
	 "acceptable_evidence": (
	  f"TODO: describe the evidence that should count as success for {case_id}."
	 ),
	 "allowed_tolerance": (
	  f"TODO: describe the allowed tolerance or write 'n/a' for {case_id}."
	 ),
	}
