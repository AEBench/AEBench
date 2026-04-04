from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ...models import PromptProfile, RuntimeMode
from ...project_config import (
    BundleRegistry,
    BundleRegistryCase,
    BundleSourceConfig,
    ProjectConfigState,
    default_user_config_path,
    load_bundle_registry_file,
    load_project_config,
)
from ...utils import safe_name
from .case_spec import write_case_spec
from ...models import CaseSpec
from .templates import render_placeholder_oracle_files

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


def write_placeholder_oracle_package(oracle_dir: Path, case_id: str) -> None:
    for relative_path, content in render_placeholder_oracle_files(case_id).items():
        (oracle_dir / relative_path).write_text(content, encoding="utf-8")


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
