from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, overload

from constants import (
    DEFAULT_AGENT_MAX_BUFFER_SIZE,
    DEFAULT_DOCKER_IMAGE,
    DEFAULT_MODEL,
    DEFAULT_OUTPUTS_DIR,
    DEFAULT_PROMPT_PROFILE,
    DEFAULT_TIMEOUT_MS,
)
from project_config import ProjectState
from settings import AgentType, LogLevel, LogRenderer, McpClientKind, McpMode

AgentOptionValue = str | int | bool | None | list[str] | dict[str, str]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    agent_type: AgentType
    max_buffer_size: int = DEFAULT_AGENT_MAX_BUFFER_SIZE
    claude_sdk_base_url: str | None = None
    api_key: str | None = None
    python_target: str | None = None
    cli_argv: list[str] = field(default_factory=list)
    cli_env: dict[str, str] = field(default_factory=dict)
    cli_shim_shells: bool = False
    cli_expose_container_shell: bool = False
    cli_expose_host_shell: bool = False
    remote_base_url: str | None = None
    remote_auth: str | None = None
    remote_protocol: str = "http"
    remote_headers: dict[str, str] = field(default_factory=dict)
    mcp_client: McpClientKind = McpClientKind.CLAUDE_CODE
    mcp_argv: list[str] = field(default_factory=list)
    mcp_env: dict[str, str] = field(default_factory=dict)
    mcp_mode: McpMode = McpMode.MCP_LOCAL

    def agent_options(self) -> dict[str, AgentOptionValue]:
        values = dataclasses.asdict(self)
        return {key: value.value if hasattr(value, "value") else value for key, value in values.items()}

    def subprocess_env(self, timeout_ms: int) -> dict[str, str]:
        env = {
            "BASH_MAX_TIMEOUT_MS": str(timeout_ms),
            "BASH_DEFAULT_TIMEOUT_MS": str(timeout_ms),
        }
        if self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key
        if self.claude_sdk_base_url:
            env["ANTHROPIC_BASE_URL"] = self.claude_sdk_base_url
        return env


@dataclass(frozen=True, slots=True)
class Config:
    default_timeout_ms: int
    default_docker_image: str
    default_model: str
    default_prompt_profile: str
    default_outputs_dir: str
    tmp_workspace_root: Path
    preserve_failed_workspace: bool
    log_level: LogLevel
    log_renderer: LogRenderer
    agent: AgentConfig


@dataclass(frozen=True, slots=True)
class AppState:
    project_state: ProjectState
    settings: Config


def resolve_settings(project_state: ProjectState, *, environ: Mapping[str, str] | None = None) -> Config:
    env = environ or os.environ
    config = project_state.config
    agent = config.agent

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
        default_model=env.get("AEBENCH_DEFAULT_MODEL", agent.default_model or DEFAULT_MODEL),
        default_prompt_profile=env.get("AEBENCH_DEFAULT_PROMPT_PROFILE", DEFAULT_PROMPT_PROFILE),
        default_outputs_dir=str(default_outputs_dir),
        tmp_workspace_root=tmp_workspace_root,
        preserve_failed_workspace=_bool_env(env, "AEBENCH_PRESERVE_FAILED_WORKSPACE", False),
        log_level=LogLevel(env.get("AEBENCH_LOG_LEVEL", (config.logging.level or LogLevel.INFO).value)),
        log_renderer=LogRenderer(env.get("AEBENCH_LOG_RENDERER", (config.logging.renderer or LogRenderer.CONSOLE).value)),
        agent=AgentConfig(
            agent_type=AgentType(env.get("AEBENCH_AGENT_KIND", (agent.agent_type or AgentType.CLAUDE_SDK).value)),
            max_buffer_size=int(env.get("AEBENCH_AGENT_MAX_BUFFER_SIZE", DEFAULT_AGENT_MAX_BUFFER_SIZE)),
            claude_sdk_base_url=env.get("ANTHROPIC_BASE_URL", agent.claude_sdk.base_url),
            api_key=env.get("ANTHROPIC_API_KEY"),
            python_target=env.get("AEBENCH_AGENT_PYTHON_TARGET", agent.python.target),
            cli_argv=_json_env(env, "AEBENCH_AGENT_CLI_ARGV", agent.cli.argv or []),
            cli_env=_json_env(env, "AEBENCH_AGENT_CLI_ENV", agent.cli.env or {}),
            cli_shim_shells=_bool_env(env, "AEBENCH_AGENT_CLI_SHIM_SHELLS", agent.cli.shim_shells),
            cli_expose_container_shell=_bool_env(env, "AEBENCH_AGENT_CLI_EXPOSE_CONTAINER_SHELL", agent.cli.expose_container_shell),
            cli_expose_host_shell=_bool_env(env, "AEBENCH_AGENT_CLI_EXPOSE_HOST_SHELL", agent.cli.expose_host_shell),
            remote_base_url=env.get("AEBENCH_AGENT_REMOTE_BASE_URL", agent.remote.base_url),
            remote_auth=env.get("AEBENCH_AGENT_REMOTE_AUTH", agent.remote.auth),
            remote_protocol=env.get("AEBENCH_AGENT_REMOTE_PROTOCOL", agent.remote.protocol or "http"),
            remote_headers=_json_env(env, "AEBENCH_AGENT_REMOTE_HEADERS", agent.remote.headers or {}),
            mcp_client=McpClientKind(env.get("AEBENCH_AGENT_MCP_CLIENT", (agent.mcp.client or McpClientKind.CLAUDE_CODE).value)),
            mcp_argv=_json_env(env, "AEBENCH_AGENT_MCP_ARGV", agent.mcp.argv or []),
            mcp_env=_json_env(env, "AEBENCH_AGENT_MCP_ENV", agent.mcp.env or {}),
            mcp_mode=McpMode(env.get("AEBENCH_AGENT_MCP_TOPOLOGY", (agent.mcp.mcp_mode or McpMode.MCP_LOCAL).value)),
        ),
    )


def _resolve_path(value: str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


@overload
def _json_env(env: Mapping[str, str], key: str, default: list[str]) -> list[str]: ...


@overload
def _json_env(env: Mapping[str, str], key: str, default: dict[str, str]) -> dict[str, str]: ...


def _json_env(env: Mapping[str, str], key: str, default: list[str] | dict[str, str]) -> list[str] | dict[str, str]:
    raw = env.get(key)
    if raw is None:
        return list(default) if isinstance(default, list) else dict(default)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{key} must be valid JSON") from exc

    if isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{key} must decode to a JSON array of strings")
        return list(value)
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{key} must decode to a JSON object of string->string")
    return dict(value)


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
