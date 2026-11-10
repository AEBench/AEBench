from __future__ import annotations

from pathlib import Path

import pytest

from runtime.agent_runner import _prompt_for_agent, prepare_agent_support_dir
from runtime.agents import AgentConfigError, discover_agents, load_agent


def _script(name: str) -> str:
	return (load_agent(name).directory / "agent.sh").read_text(encoding="utf-8")


def test_codex_agents_are_built_in() -> None:
	catalog = discover_agents()
	assert {"codex", "codex_non_api"} <= set(catalog.agents)


@pytest.mark.parametrize("name", ["codex", "codex_non_api"])
def test_codex_script_streams_json_without_secret_echoes(name: str) -> None:
	script = _script(name)
	assert "printf '%s' \"$PROMPT\" | codex --search exec --json" in script
	assert 'model_reasoning_effort=\\"$AEBENCH_REASONING_EFFORT\\"' in script
	assert "model_reasoning_summary=detailed" in script
	assert "--skip-git-repo-check --yolo" in script
	assert '--model "$AEBENCH_MODEL"' in script
	assert 'CODEX_HOME="$AEBENCH_STATE_DIR/.codex"' in script
	assert "echo $OPENAI_API_KEY" not in script
	assert "echo $CODEX_API_KEY" not in script
	assert load_agent(name).config.reasoning_effort == "high"


def test_codex_api_key_is_passed_to_the_cli() -> None:
	assert 'export CODEX_API_KEY="$OPENAI_API_KEY"' in _script("codex")


def test_codex_prompt_is_unchanged() -> None:
	assert _prompt_for_agent(load_agent("codex"), "do the task") == "do the task"


@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "CODEX_API_KEY"])
def test_codex_api_key_is_delivered_as_a_secret(tmp_path: Path, variable: str) -> None:
	support_dir = prepare_agent_support_dir(
		load_agent("codex"), tmp_path / "run", environ={variable: "openai-secret"}
	)
	secrets = support_dir / "aebench" / "secrets.sh"
	assert secrets.read_text(encoding="utf-8") == "export OPENAI_API_KEY=openai-secret\n"
	assert secrets.stat().st_mode & 0o777 == 0o600


def test_missing_api_key_names_the_variables(tmp_path: Path) -> None:
	with pytest.raises(AgentConfigError, match="OPENAI_API_KEY or CODEX_API_KEY"):
		prepare_agent_support_dir(load_agent("codex"), tmp_path, environ={})


def test_subscription_auth_is_copied_to_private_state_dir(tmp_path: Path) -> None:
	auth = tmp_path / "auth.json"
	auth.write_text('{"tokens":{}}', encoding="utf-8")
	support_dir = prepare_agent_support_dir(
		load_agent("codex_non_api"),
		tmp_path / "run",
		environ={"AEBENCH_CODEX_AUTH_FILE": str(auth)},
	)

	target = support_dir / "state" / ".codex" / "auth.json"
	assert target.read_text(encoding="utf-8") == '{"tokens":{}}'
	assert target.stat().st_mode & 0o777 == 0o600
	assert not (support_dir / "aebench" / "secrets.sh").exists()


def test_subscription_auth_defaults_to_codex_home(tmp_path: Path) -> None:
	codex_home = tmp_path / "codex-home"
	codex_home.mkdir()
	(codex_home / "auth.json").write_text("{}", encoding="utf-8")
	support_dir = prepare_agent_support_dir(
		load_agent("codex_non_api"),
		tmp_path / "run",
		environ={"CODEX_HOME": str(codex_home)},
	)
	assert (support_dir / "state" / ".codex" / "auth.json").is_file()


def test_missing_subscription_auth_does_not_leave_support_dir(tmp_path: Path) -> None:
	parent = tmp_path / "run"
	with pytest.raises(AgentConfigError, match="codex login") as caught:
		prepare_agent_support_dir(
			load_agent("codex_non_api"),
			parent,
			environ={"AEBENCH_CODEX_AUTH_FILE": str(tmp_path / "missing.json")},
		)
	assert "missing.json" in str(caught.value)
	assert not (parent / "agent-support").exists()
