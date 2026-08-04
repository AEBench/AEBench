from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from models import ArtifactRequirementsConfig, RuntimeConfig, RuntimeMode, TaskConfig
from runtime.agent_runner import (
	_agent_env,
	_prompt_for_agent,
	_solve_script,
	prepare_agent_home,
	run_agent,
)
from runtime.backend import DockerRuntime, LocalRuntime


class FakeRuntime:
	path_separator = ":"

	def __init__(self) -> None:
		self.command: list[str] = []
		self.cwd: str | None = None
		self.env: dict[str, str] = {}
		self.stdin_text: str | None = None
		self.has_timeout = True

	def resolve_executable(
		self,
		executable: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		_ = cwd, env
		return f"/usr/bin/{executable}" if self.has_timeout and executable == "timeout" else None

	def run_process_to_file(
		self,
		cmd: list[str],
		*,
		output_path: Path,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float = 5.0,
	) -> subprocess.CompletedProcess[str]:
		_ = timeout
		self.command = cmd
		self.cwd = cwd
		self.env = dict(env or {})
		self.stdin_text = stdin_text
		output_path.write_text('{"type":"result"}\n', encoding="utf-8")
		return subprocess.CompletedProcess(cmd, 0, "", "")

	def run_process(
		self,
		cmd: list[str],
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float = 5.0,
	) -> subprocess.CompletedProcess[str]:
		_ = cwd, env, stdin_text, timeout
		return subprocess.CompletedProcess(cmd, 0, "", "")


def test_codex_script_streams_json_without_secret_echoes() -> None:
	script = _solve_script("codex")
	assert "printf '%s' \"$PROMPT\" | codex --search exec --json" in script
	assert "model_reasoning_summary=detailed" in script
	assert "--skip-git-repo-check --yolo" in script
	assert "echo $OPENAI_API_KEY" not in script
	assert "echo $CODEX_API_KEY" not in script


def test_claude_script_streams_json_without_permission_prompts() -> None:
	script = _solve_script("claude")
	assert 'BASH_MAX_TIMEOUT_MS="36000000"' in script
	assert "claude --print --verbose" in script
	assert "--output-format stream-json --thinking-display summarized" in script
	assert "--dangerously-skip-permissions" in script


def test_claude_prompt_includes_noninteractive_guidance() -> None:
	prompt = _prompt_for_agent("claude", "do the task\n")
	assert prompt.startswith("do the task\n\n")
	assert "make sure every process you are running finishes" in prompt
	assert _prompt_for_agent("codex", "do the task") == "do the task"
	assert _prompt_for_agent("claude_non_api", "do the task") == "do the task"


def test_runner_passes_harness_contract_and_saves_raw_output(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("OPENAI_API_KEY", "secret")
	runtime = FakeRuntime()
	output_path = tmp_path / "solve_out.txt"

	result = run_agent(
		"codex",
		model="gpt-test",
		prompt="do the task",
		runtime=runtime,  # type: ignore[arg-type]
		cwd="/repo",
		runtime_home="/home/agent",
		timeout_seconds=600,
		output_path=output_path,
	)

	assert runtime.command == [
		"timeout",
		"--signal=TERM",
		"--kill-after=30s",
		"600s",
		"bash",
		"-s",
	]
	assert runtime.cwd == "/repo"
	assert runtime.env["HOME"] == "/home/agent"
	assert runtime.env["PROMPT"] == "do the task"
	assert runtime.env["AGENT_CONFIG"] == "gpt-test"
	assert runtime.env["CODEX_API_KEY"] == "secret"
	assert "ANTHROPIC_API_KEY" not in runtime.env
	assert runtime.stdin_text == _solve_script("codex")
	assert result.exit_code == 0
	assert output_path.read_text(encoding="utf-8") == '{"type":"result"}\n'


def test_subscription_auth_is_copied_to_private_run_home(tmp_path: Path) -> None:
	auth = tmp_path / "auth.json"
	auth.write_text('{"tokens":{}}', encoding="utf-8")
	home = prepare_agent_home(
		"codex_non_api",
		tmp_path / "run",
		environ={"AEBENCH_CODEX_AUTH_FILE": str(auth)},
	)

	target = home / ".codex" / "auth.json"
	assert target.read_text(encoding="utf-8") == '{"tokens":{}}'
	assert target.stat().st_mode & 0o777 == 0o600


def test_claude_subscription_token_is_written_to_private_run_home(tmp_path: Path) -> None:
	home = prepare_agent_home(
		"claude_non_api",
		tmp_path / "run",
		environ={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret"},
	)

	target = home / "oauth_token"
	assert target.read_text(encoding="utf-8") == "oauth-secret"
	assert target.stat().st_mode & 0o777 == 0o600


def test_missing_subscription_auth_does_not_leave_run_home(tmp_path: Path) -> None:
	parent = tmp_path / "run"
	with pytest.raises(RuntimeError, match="Codex subscription auth not found"):
		prepare_agent_home(
			"codex_non_api",
			parent,
			environ={"AEBENCH_CODEX_AUTH_FILE": str(tmp_path / "missing.json")},
		)
	assert not (parent / "agent-home").exists()


def test_docker_agent_environment_does_not_copy_host_runtime(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
	monkeypatch.setenv("PATH", "/host/bin")
	env = _agent_env(
		"codex",
		model="gpt-test",
		prompt="do work",
		runtime_home="/home/agent",
		include_host_runtime=False,
	)
	assert env == {
		"AGENT_CONFIG": "gpt-test",
		"CODEX_API_KEY": "openai-secret",
		"HOME": "/home/agent",
		"PROMPT": "do work",
	}


def test_local_runtime_streams_combined_output_to_file(tmp_path: Path) -> None:
	output_path = tmp_path / "runner_output.log"
	result = LocalRuntime(workspace=str(tmp_path)).run_process_to_file(
		["bash", "-c", "printf stdout; printf stderr >&2"],
		output_path=output_path,
	)
	assert result.returncode == 0
	assert output_path.read_text(encoding="utf-8") == "stdoutstderr"


def test_local_agent_process_does_not_inherit_unlisted_host_environment(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("AEBENCH_HOST_SECRET", "must-not-leak")
	output_path = tmp_path / "runner_output.log"
	result = LocalRuntime(workspace=str(tmp_path)).run_process_to_file(
		["sh", "-c", 'printf %s "${AEBENCH_HOST_SECRET-unset}"'],
		output_path=output_path,
		env={"PATH": os.environ["PATH"]},
	)

	assert result.returncode == 0
	assert output_path.read_text(encoding="utf-8") == "unset"


def test_docker_artifact_workspace_mount_preserves_host_path(tmp_path: Path) -> None:
	task = TaskConfig(
		id="docker-artifact",
		runtime=RuntimeConfig(mode=RuntimeMode.DOCKER, image="aebench-agent:latest"),
		artifact_requirements=ArtifactRequirementsConfig(docker=True),
	)
	home = tmp_path / "agent-home"
	home.mkdir()
	runtime = DockerRuntime(container_name="test-container", resolved_image="aebench-agent:latest")
	session = SimpleNamespace(
		run_spec=task,
		host_workspace=tmp_path,
		runtime_workspace=str(tmp_path),
		host_refs=None,
		host_agent_home=home,
		runtime_agent_home="/home/agent",
	)

	command = runtime._docker_run_command(session)  # type: ignore[arg-type]
	assert f"{tmp_path}:{tmp_path}" in command
	assert f"{home}:/home/agent" in command
	assert command[command.index("-w") + 1] == str(tmp_path)
	assert "/var/run/docker.sock:/var/run/docker.sock" in command


def test_docker_stop_preserves_container_until_cleanup(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	commands: list[list[str]] = []

	def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
		commands.append(cmd)
		return subprocess.CompletedProcess(cmd, 0, "", "")

	monkeypatch.setattr(subprocess, "run", fake_run)
	runtime = DockerRuntime(
		container_id="container-id",
		container_name="container-name",
		resolved_image="aebench-agent:latest",
	)
	session = SimpleNamespace(
		task_id="test",
		run_spec=TaskConfig(id="test", runtime=RuntimeConfig(mode="docker")),
	)

	runtime.stop(session)  # type: ignore[arg-type]
	assert runtime.container_id == "container-id"
	assert runtime.container_stopped is True
	assert commands == [["docker", "stop", "--time", "30", "container-id"]]

	saved_image = runtime.snapshot(session)  # type: ignore[arg-type]
	assert saved_image is not None
	assert commands[1] == ["docker", "commit", "container-id", saved_image]

	runtime.cleanup(session)  # type: ignore[arg-type]
	assert runtime.container_id is None
	assert runtime.container_removed is True
	assert commands[2] == ["docker", "rm", "-f", "container-id"]
	assert commands[3] == ["docker", "rmi", "-f", saved_image]
