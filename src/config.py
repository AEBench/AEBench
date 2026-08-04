from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from constants import (
	DEFAULT_DOCKER_IMAGE,
	DEFAULT_OUTPUTS_DIR,
	DEFAULT_PROMPT_PROFILE,
	DEFAULT_TIMEOUT_MS,
)
from project_config import ProjectState
from settings import LogLevel, LogRenderer


@dataclass(frozen=True, slots=True)
class Config:
	default_timeout_ms: int
	default_docker_image: str
	default_prompt_profile: str
	default_outputs_dir: str
	tmp_workspace_root: Path
	preserve_failed_workspace: bool
	log_level: LogLevel
	log_renderer: LogRenderer


@dataclass(frozen=True, slots=True)
class AppState:
	project_state: ProjectState
	settings: Config


def resolve_settings(
	project_state: ProjectState, *, environ: Mapping[str, str] | None = None
) -> Config:
	env = environ or os.environ
	config = project_state.config

	tmp_workspace_root = _resolve_path(
		env.get("AEBENCH_EPHEMERAL_WORKSPACE_ROOT", "/tmp/aebench-workspaces"),
		base=project_state.root,
	)
	default_outputs_dir = _resolve_path(
		env.get("AEBENCH_DEFAULT_OUTPUTS_DIR", DEFAULT_OUTPUTS_DIR),
		base=project_state.root,
	)

	return Config(
		default_timeout_ms=int(env.get("AEBENCH_DEFAULT_TIMEOUT_MS", DEFAULT_TIMEOUT_MS)),
		default_docker_image=env.get("AEBENCH_DEFAULT_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE),
		default_prompt_profile=env.get("AEBENCH_DEFAULT_PROMPT_PROFILE", DEFAULT_PROMPT_PROFILE),
		default_outputs_dir=str(default_outputs_dir),
		tmp_workspace_root=tmp_workspace_root,
		preserve_failed_workspace=_bool_env(env, "AEBENCH_PRESERVE_FAILED_WORKSPACE", False),
		log_level=LogLevel(
			env.get("AEBENCH_LOG_LEVEL", (config.logging.level or LogLevel.INFO).value)
		),
		log_renderer=LogRenderer(
			env.get("AEBENCH_LOG_RENDERER", (config.logging.renderer or LogRenderer.CONSOLE).value)
		),
	)


def _resolve_path(value: str, *, base: Path) -> Path:
	path = Path(value).expanduser()
	return path.resolve() if path.is_absolute() else (base / path).resolve()


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
	raw = env.get(key)
	if raw is None:
		return default
	return raw.strip().lower() in {"1", "true", "yes", "on"}
