from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from runtime.agent_runner import (
	_prompt_for_agent,
	check_agent_requirements,
	prepare_agent_support_dir,
)
from runtime.agents import AgentConfigError, discover_agents, load_agent


def _script(name: str) -> str:
	return (load_agent(name).directory / "agent.sh").read_text(encoding="utf-8")


class _NothingInstalled:
	def resolve_executable(
		self, executable: str, *, cwd: str | None = None, env: Mapping[str, str] | None = None
	) -> str | None:
		_ = executable, cwd, env
		return None

	def run_process(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
		return subprocess.CompletedProcess(cmd, 0, "", "")


def test_claude_agents_are_built_in() -> None:
	catalog = discover_agents()
	assert {"claude", "claude_non_api"} <= set(catalog.agents)


@pytest.mark.parametrize("name", ["claude", "claude_non_api"])
def test_claude_script_streams_json_without_permission_prompts(name: str) -> None:
	script = _script(name)
	assert "claude --print --verbose" in script
	assert '--model "$AEBENCH_MODEL"' in script
	assert "--output-format stream-json" in script
	assert "--thinking-display summarized" in script
	assert "--dangerously-skip-permissions" in script
	assert 'CLAUDE_CONFIG_DIR="$AEBENCH_STATE_DIR/.claude"' in script


def test_api_key_agent_allows_long_foreground_commands() -> None:
	assert 'BASH_MAX_TIMEOUT_MS="36000000"' in _script("claude")


def test_subscription_agent_disables_background_tasks_and_long_timeouts() -> None:
	script = _script("claude_non_api")
	assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1" in script
	assert "BASH_DEFAULT_TIMEOUT_MS=3600000" in script
	assert "BASH_MAX_TIMEOUT_MS=36000000" in script
	assert 'ANTHROPIC_API_KEY=""' in script


def test_reasoning_effort_is_set_only_for_the_subscription_agent() -> None:
	assert load_agent("claude").config.reasoning_effort is None
	assert load_agent("claude_non_api").config.reasoning_effort == "high"
	assert '--effort "$AEBENCH_REASONING_EFFORT"' in _script("claude_non_api")


@pytest.mark.parametrize("name", ["claude", "claude_non_api"])
def test_claude_prompt_includes_noninteractive_guidance(name: str) -> None:
	prompt = _prompt_for_agent(load_agent(name), "do the task\n")
	assert prompt == (
		"do the task\n\nYou cannot receive interactive input. Wait for every process that "
		"you start to finish before you write your final response."
	)


def test_missing_cli_points_at_the_install_script() -> None:
	with pytest.raises(RuntimeError, match="requires claude") as caught:
		check_agent_requirements(
			load_agent("claude"),
			_NothingInstalled(),  # type: ignore[arg-type]
		)
	assert "agents/claude/install.sh" in str(caught.value)


def test_api_key_is_delivered_as_a_secret(tmp_path: Path) -> None:
	support_dir = prepare_agent_support_dir(
		load_agent("claude"),
		tmp_path / "run",
		environ={"ANTHROPIC_API_KEY": "anthropic-secret"},
	)
	secrets = support_dir / "aebench" / "secrets.sh"
	assert secrets.read_text(encoding="utf-8") == "export ANTHROPIC_API_KEY=anthropic-secret\n"
	assert secrets.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("source", ["env", "file"])
def test_subscription_token_is_delivered_as_a_secret(tmp_path: Path, source: str) -> None:
	token_file = tmp_path / "token"
	token_file.write_text("oauth-secret\n", encoding="utf-8")
	environ = (
		{"CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret"}
		if source == "env"
		else {"AEBENCH_CLAUDE_OAUTH_TOKEN_FILE": str(token_file)}
	)
	support_dir = prepare_agent_support_dir(
		load_agent("claude_non_api"), tmp_path / "run", environ=environ
	)

	secrets = support_dir / "aebench" / "secrets.sh"
	assert secrets.read_text(encoding="utf-8") == "export CLAUDE_CODE_OAUTH_TOKEN=oauth-secret\n"
	assert secrets.stat().st_mode & 0o777 == 0o600


def test_subscription_agent_does_not_read_the_token_file_itself() -> None:
	assert "AEBENCH_CLAUDE_OAUTH_TOKEN_FILE" not in _script("claude_non_api")


def test_missing_subscription_token_is_reported(tmp_path: Path) -> None:
	with pytest.raises(AgentConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
		prepare_agent_support_dir(load_agent("claude_non_api"), tmp_path / "run", environ={})
