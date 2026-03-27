from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ...project_config import ProjectConfigState
from ...settings import AgentKind, LogLevel, LogRenderer, McpClientKind, McpTopology


@dataclass(frozen=True, slots=True)
class ResolvedAgentConfig:
	kind: AgentKind
	max_buffer_size: int
	claude_sdk_base_url: str | None
	api_key: str | None
	python_target: str | None
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
	mcp_topology: McpTopology = McpTopology.MCP_COLOCATED

	def driver_options(self) -> dict[str, object]:
		return {
		 "kind": self.kind.value,
		 "claude_sdk_base_url": self.claude_sdk_base_url,
		 "python_target": self.python_target,
		 "cli_argv": list(self.cli_argv),
		 "cli_env": dict(self.cli_env),
		 "cli_shim_shells": self.cli_shim_shells,
		 "cli_expose_container_shell": self.cli_expose_container_shell,
		 "cli_expose_host_shell": self.cli_expose_host_shell,
		 "remote_base_url": self.remote_base_url,
		 "remote_auth": self.remote_auth,
		 "remote_protocol": self.remote_protocol,
		 "remote_headers": dict(self.remote_headers),
		 "mcp_client": self.mcp_client.value,
		 "mcp_argv": list(self.mcp_argv),
		 "mcp_env": dict(self.mcp_env),
		 "mcp_topology": self.mcp_topology.value,
		 "max_buffer_size": self.max_buffer_size,
		}

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
class ResolvedSettings:
	default_timeout_ms: int
	default_docker_image: str
	default_model: str
	default_prompt_profile: str
	default_outputs_dir: str
	ephemeral_workspace_root: Path
	preserve_failed_workspace: bool
	log_level: LogLevel
	log_renderer: LogRenderer
	agent: ResolvedAgentConfig


@dataclass(frozen=True, slots=True)
class AppContext:
	project_state: ProjectConfigState
	settings: ResolvedSettings


def resolve_settings(
 project_state: ProjectConfigState,
 *,
 environ: Mapping[str, str] | None = None,
) -> ResolvedSettings:
	env = environ or os.environ
	config = project_state.config
	agent_config = config.agent

	default_timeout_ms = int(env.get("ARTEVALBENCH_DEFAULT_TIMEOUT_MS", 345_600_000))
	default_docker_image = env.get("ARTEVALBENCH_DEFAULT_DOCKER_IMAGE", "artevalbench-agent:latest")
	default_model = env.get(
	 "ARTEVALBENCH_DEFAULT_MODEL",
	 agent_config.default_model or "claude-sonnet-4-5-20250929",
	)
	default_prompt_profile = env.get("ARTEVALBENCH_DEFAULT_PROMPT_PROFILE", "artifact-eval-v1")
	default_outputs_dir = env.get("ARTEVALBENCH_DEFAULT_OUTPUTS_DIR", "./outputs")
	ephemeral_workspace_root = Path(
	 env.get("ARTEVALBENCH_EPHEMERAL_WORKSPACE_ROOT", "/tmp/artevalbench-workspaces")
	).expanduser()
	preserve_failed_workspace = env.get(
	 "ARTEVALBENCH_PRESERVE_FAILED_WORKSPACE", "false"
	).lower() in {
	 "1",
	 "true",
	 "yes",
	 "on",
	}
	log_level = LogLevel(
	 env.get("ARTEVALBENCH_LOG_LEVEL", (config.logging.level or LogLevel.INFO).value)
	)
	log_renderer = LogRenderer(
	 env.get("ARTEVALBENCH_LOG_RENDERER", (config.logging.renderer or LogRenderer.CONSOLE).value)
	)

	return ResolvedSettings(
	 default_timeout_ms=default_timeout_ms,
	 default_docker_image=default_docker_image,
	 default_model=default_model,
	 default_prompt_profile=default_prompt_profile,
	 default_outputs_dir=default_outputs_dir,
	 ephemeral_workspace_root=ephemeral_workspace_root.resolve(),
	 preserve_failed_workspace=preserve_failed_workspace,
	 log_level=log_level,
	 log_renderer=log_renderer,
	 agent=ResolvedAgentConfig(
	  kind=AgentKind(
	   env.get(
	    "ARTEVALBENCH_AGENT_KIND", (agent_config.kind or AgentKind.CLAUDE_SDK).value
	   )
	  ),
	  max_buffer_size=int(env.get("ARTEVALBENCH_AGENT_MAX_BUFFER_SIZE", 8 * 1024 * 1024)),
	  claude_sdk_base_url=env.get("ANTHROPIC_BASE_URL", agent_config.claude_sdk.base_url),
	  api_key=env.get("ANTHROPIC_API_KEY"),
	  python_target=env.get("ARTEVALBENCH_AGENT_PYTHON_TARGET", agent_config.python.target),
	  cli_argv=_json_list_env(
	   env, "ARTEVALBENCH_AGENT_CLI_ARGV", agent_config.cli.argv or []
	  ),
	  cli_env=_json_dict_env(env, "ARTEVALBENCH_AGENT_CLI_ENV", agent_config.cli.env or {}),
	  cli_shim_shells=_bool_env(
	   env, "ARTEVALBENCH_AGENT_CLI_SHIM_SHELLS", agent_config.cli.shim_shells
	  ),
	  cli_expose_container_shell=_bool_env(
	   env,
	   "ARTEVALBENCH_AGENT_CLI_EXPOSE_CONTAINER_SHELL",
	   agent_config.cli.expose_container_shell,
	  ),
	  cli_expose_host_shell=_bool_env(
	   env,
	   "ARTEVALBENCH_AGENT_CLI_EXPOSE_HOST_SHELL",
	   agent_config.cli.expose_host_shell,
	  ),
	  remote_base_url=env.get(
	   "ARTEVALBENCH_AGENT_REMOTE_BASE_URL", agent_config.remote.base_url
	  ),
	  remote_auth=env.get("ARTEVALBENCH_AGENT_REMOTE_AUTH", agent_config.remote.auth),
	  remote_protocol=env.get(
	   "ARTEVALBENCH_AGENT_REMOTE_PROTOCOL", agent_config.remote.protocol or "http"
	  ),
	  remote_headers=_json_dict_env(
	   env, "ARTEVALBENCH_AGENT_REMOTE_HEADERS", agent_config.remote.headers or {}
	  ),
	  mcp_client=McpClientKind(
	   env.get(
	    "ARTEVALBENCH_AGENT_MCP_CLIENT",
	    (agent_config.mcp.client or McpClientKind.CLAUDE_CODE).value,
	   )
	  ),
	  mcp_argv=_json_list_env(
	   env, "ARTEVALBENCH_AGENT_MCP_ARGV", agent_config.mcp.argv or []
	  ),
	  mcp_env=_json_dict_env(env, "ARTEVALBENCH_AGENT_MCP_ENV", agent_config.mcp.env or {}),
	  mcp_topology=McpTopology(
	   env.get(
	    "ARTEVALBENCH_AGENT_MCP_TOPOLOGY",
	    (agent_config.mcp.topology or McpTopology.MCP_COLOCATED).value,
	   )
	  ),
	 ),
	)


def _json_list_env(env: Mapping[str, str], key: str, default: list[str]) -> list[str]:
	raw = env.get(key)
	if raw is None:
		return list(default)
	try:
		value = json.loads(raw)
	except json.JSONDecodeError:
		return list(default)
	if isinstance(value, list):
		return [item for item in value if isinstance(item, str)]
	return list(default)


def _json_dict_env(env: Mapping[str, str], key: str, default: dict[str, str]) -> dict[str, str]:
	raw = env.get(key)
	if raw is None:
		return dict(default)
	try:
		value = json.loads(raw)
	except json.JSONDecodeError:
		return dict(default)
	if isinstance(value, dict):
		return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
	return dict(default)


def _bool_env(env: Mapping[str, str], key: str, default: bool) -> bool:
	raw = env.get(key)
	if raw is None:
		return default
	return raw.strip().lower() in {"1", "true", "yes", "on"}
