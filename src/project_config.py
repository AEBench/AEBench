from __future__ import annotations

import json
import sys
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from settings import AgentType, LogLevel, LogRenderer, McpClientKind, McpMode

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_DEFAULT_CASE_RUNS_DIR = "~/.cache/aebench/case-runs"
_DEFAULT_USER_CONFIG_PATH = "~/.config/aebench/config.toml"
_REGISTRY_FILENAME = "cases.json"


def _require_relative(path_text: str, field_name: str) -> None:
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} must be a relative path")


class ArtifactMode(str, Enum):
    VENDOR = "vendor"
    POINTER = "pointer"
    HYBRID = "hybrid"


class LoggingConfig(_Model):
    level: LogLevel | None = None
    renderer: LogRenderer | None = None


class AgentClaudeSdkConfig(_Model):
    base_url: str | None = None


class AgentPythonConfig(_Model):
    target: str | None = None


class AgentCliConfig(_Model):
    argv: list[str] | None = None
    env: dict[str, str] | None = None
    shim_shells: bool = False
    expose_container_shell: bool = False
    expose_host_shell: bool = False


class AgentRemoteConfig(_Model):
    base_url: str | None = None
    auth: str | None = None
    protocol: str | None = None
    headers: dict[str, str] | None = None


class AgentMcpConfig(_Model):
    client: McpClientKind | None = None
    argv: list[str] | None = None
    env: dict[str, str] | None = None
    mcp_mode: McpMode | None = None


class AgentSettings(_Model):
    agent_type: AgentType | None = None
    default_model: str | None = None
    claude_sdk: AgentClaudeSdkConfig = Field(default_factory=AgentClaudeSdkConfig)
    python: AgentPythonConfig = Field(default_factory=AgentPythonConfig)
    cli: AgentCliConfig = Field(default_factory=AgentCliConfig)
    remote: AgentRemoteConfig = Field(default_factory=AgentRemoteConfig)
    mcp: AgentMcpConfig = Field(default_factory=AgentMcpConfig)


class GitCacheConfig(_Model):
    root: str = "~/.cache/aebench/git"
    max_size_bytes: int = 10 * 1024 * 1024 * 1024
    prune_on_fetch: bool = True
    symlink_artifact: bool = True

    def resolve_root(self, project_root: Path) -> Path:
        path = Path(self.root).expanduser()
        return path.resolve() if path.is_absolute() else (project_root / path).resolve()


class GitCacheOverrideConfig(_Model):
    root: str | None = None
    max_size_bytes: int | None = None
    prune_on_fetch: bool | None = None
    symlink_artifact: bool | None = None


class CacheConfig(_Model):
    git: GitCacheConfig = Field(default_factory=GitCacheConfig)


class CacheOverrideConfig(_Model):
    git: GitCacheOverrideConfig = Field(default_factory=GitCacheOverrideConfig)


class BundleSourceConfig(_Model):
    url: str | None = None
    ref: str | None = None
    bundles_subdir: str = "cases"

    @model_validator(mode="after")
    def _validate_relative_subdir(self) -> "BundleSourceConfig":
        _require_relative(self.bundles_subdir, "bundle_source.bundles_subdir")
        return self


class BundleSourceOverrideConfig(_Model):
    url: str | None = None
    ref: str | None = None
    bundles_subdir: str | None = None

    @model_validator(mode="after")
    def _validate_relative_subdir(self) -> "BundleSourceOverrideConfig":
        if self.bundles_subdir is not None:
            _require_relative(self.bundles_subdir, "bundle_source.bundles_subdir")
        return self


class RepoLaunchConfig(_Model):
    enabled: bool = False
    source: str | None = None


class RepoLaunchOverrideConfig(_Model):
    enabled: bool | None = None
    source: str | None = None


class BundleRegistryCase(_Model):
    path: str

    @model_validator(mode="after")
    def _validate_path(self) -> "BundleRegistryCase":
        _require_relative(self.path, "registry case path")
        return self


class BundleRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = 1
    bundles_dir: str = Field(default="cases", alias="cases_dir", validation_alias="cases_dir")
    default_source: BundleSourceConfig = Field(default_factory=BundleSourceConfig)
    cases: dict[str, BundleRegistryCase] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_old_name(cls, values: object) -> object:
        if isinstance(values, dict) and "bundles_dir" in values and "cases_dir" not in values:
            values = dict(values)
            values["cases_dir"] = values.pop("bundles_dir")
        return values

    @model_validator(mode="after")
    def _validate_bundles_dir(self) -> "BundleRegistry":
        _require_relative(self.bundles_dir, "bundles_dir")
        return self


class UserConfig(_Model):
    case_runs_dir: str | None = None
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    cache: CacheOverrideConfig = Field(default_factory=CacheOverrideConfig)


class WorkspaceConfig(_Model):
    artifact_mode: ArtifactMode | None = None
    bundle_source: BundleSourceOverrideConfig = Field(default_factory=BundleSourceOverrideConfig)
    bundles_dir: str | None = None
    case_runs_dir: str | None = None
    default_bundle_layout: str | None = None
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    cache: CacheOverrideConfig = Field(default_factory=CacheOverrideConfig)
    repo_launch: RepoLaunchOverrideConfig = Field(default_factory=RepoLaunchOverrideConfig)


class ProjectConfig(_Model):
    bundles_dir: str = "cases"
    case_runs_dir: str = _DEFAULT_CASE_RUNS_DIR
    artifact_mode: ArtifactMode = ArtifactMode.VENDOR
    default_bundle_layout: str = "structured"
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    bundle_source: BundleSourceConfig = Field(default_factory=BundleSourceConfig)
    repo_launch: RepoLaunchConfig = Field(default_factory=RepoLaunchConfig)

    def resolve_bundles_dir(self, root: Path) -> Path:
        return (root / self.bundles_dir).resolve()

    def resolve_case_runs_dir(self, root: Path) -> Path:
        path = Path(self.case_runs_dir).expanduser()
        return path.resolve() if path.is_absolute() else (root / path).resolve()


class ProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    root: Path
    path: Path | None
    user_path: Path | None
    registry_path: Path | None
    config: ProjectConfig
    registry: BundleRegistry


def load_project_config(start: Path | None = None, *, config_path: Path | None = None) -> ProjectState:
    override_path = config_path.expanduser().resolve() if config_path is not None else None
    start_path = (start or Path.cwd()).resolve()
    if start_path.is_file():
        start_path = start_path.parent

    root, workspace_path, registry_path = _discover_workspace(start_path)
    workspace = _load_workspace_config(workspace_path)
    if override_path is not None:
        workspace = _merge_workspace_override(workspace, _load_workspace_config(override_path))

    user_path = default_user_config_path()
    user = _load_user_config(user_path if user_path.is_file() else None)
    registry, effective_registry_path = _load_registry(registry_path)
    config = _merge_project_config(registry, workspace, user)

    return ProjectState(
        root=root,
        path=override_path or workspace_path,
        user_path=user_path if user_path.is_file() else None,
        registry_path=effective_registry_path,
        config=config,
        registry=registry,
    )


def project_config_dict(state: ProjectState) -> dict[str, Any]:
    return state.config.model_dump(mode="json")


def default_user_config_path() -> Path:
    return Path(_DEFAULT_USER_CONFIG_PATH).expanduser()


def load_bundle_registry_file(path: Path) -> BundleRegistry:
    with path.open("r", encoding="utf-8") as handle:
        return BundleRegistry.model_validate(json.load(handle))


def load_packaged_bundle_registry() -> BundleRegistry | None:
    try:
        resource = resources.files("aebench").joinpath(_REGISTRY_FILENAME)
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    if not resource.is_file():
        return None
    with resource.open("r", encoding="utf-8") as handle:
        return BundleRegistry.model_validate(json.load(handle))


def _discover_workspace(start: Path) -> tuple[Path, Path | None, Path | None]:
    for directory in (start, *start.parents):
        workspace = directory / "aebench.toml"
        registry = directory / _REGISTRY_FILENAME
        if workspace.is_file() or registry.is_file():
            return directory, workspace.resolve() if workspace.is_file() else None, registry.resolve() if registry.is_file() else None
    return start, None, None


def _load_workspace_config(path: Path | None) -> WorkspaceConfig:
    if path is None:
        return WorkspaceConfig()
    with path.open("rb") as handle:
        return WorkspaceConfig.model_validate(tomllib.load(handle))


def _load_user_config(path: Path | None) -> UserConfig:
    if path is None:
        return UserConfig()
    with path.open("rb") as handle:
        return UserConfig.model_validate(tomllib.load(handle))


def _load_registry(path: Path | None) -> tuple[BundleRegistry, Path | None]:
    if path is not None:
        return load_bundle_registry_file(path), path
    packaged = load_packaged_bundle_registry()
    return (packaged, None) if packaged is not None else (BundleRegistry(), None)


def _merge_project_config(registry: BundleRegistry, workspace: WorkspaceConfig, user: UserConfig) -> ProjectConfig:
    config = ProjectConfig(bundles_dir=registry.bundles_dir, bundle_source=registry.default_source)
    config = config.model_copy(
        update={
            "logging": _apply_overrides(config.logging, user.logging),
            "agent": _apply_overrides(config.agent, user.agent),
            "cache": config.cache.model_copy(update={"git": _apply_overrides(config.cache.git, user.cache.git)}),
            "case_runs_dir": user.case_runs_dir or config.case_runs_dir,
        }
    )

    updates: dict[str, Any] = {
        "logging": _apply_overrides(config.logging, workspace.logging),
        "agent": _apply_overrides(config.agent, workspace.agent),
        "cache": config.cache.model_copy(update={"git": _apply_overrides(config.cache.git, workspace.cache.git)}),
        "bundle_source": _apply_overrides(config.bundle_source, workspace.bundle_source),
        "repo_launch": _apply_overrides(config.repo_launch, workspace.repo_launch),
    }
    for name in ("bundles_dir", "case_runs_dir", "artifact_mode", "default_bundle_layout"):
        value = getattr(workspace, name)
        if value is not None:
            updates[name] = value
    return config.model_copy(update=updates)


def _apply_overrides(base: _ModelT, override: BaseModel) -> _ModelT:
    merged = _deep_merge_dict(base.model_dump(mode="json"), override.model_dump(exclude_none=True, mode="json"))
    return base.__class__.model_validate(merged)


def _merge_workspace_override(base: WorkspaceConfig, override: WorkspaceConfig) -> WorkspaceConfig:
    return WorkspaceConfig.model_validate(
        _deep_merge_dict(base.model_dump(mode="json"), override.model_dump(exclude_unset=True, mode="json"))
    )


def _deep_merge_dict(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        merged[key] = _deep_merge_dict(current, value) if isinstance(current, dict) and isinstance(value, dict) else value
    return merged
