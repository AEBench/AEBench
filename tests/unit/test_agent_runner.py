from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from models import (
	ArtifactRequirementsConfig,
	RuntimeConfig,
	RuntimeMode,
	TaskConfig,
)
from runtime import agent_runner
from runtime.agent_runner import (
	_agent_env,
	_agent_shell_command,
	_prompt_for_agent,
	_timeout_command,
	check_agent_requirements,
	prepare_agent_runtime,
	prepare_agent_support_dir,
	run_agent,
)
from runtime.agents import AgentConfigError, AgentDefinition, load_agent_dir
from runtime.backend import DockerRuntime, LocalRuntime

_PIPELINE = (
	'bash "$AEBENCH_AGENT_SUPPORT_DIR"/aebench/entry.sh </dev/null 2>&1 | '
	'python3 "$AEBENCH_AGENT_SUPPORT_DIR"/aebench/timestamp_lines.py'
)


class FakeRuntime:
	path_separator = ":"

	def __init__(self, installed: tuple[str, ...] = ("timeout",)) -> None:
		self.command: list[str] = []
		self.cwd: str | None = None
		self.env: dict[str, str] = {}
		self.stdin_text: str | None = None
		self.installed = set(installed)

	def resolve_executable(
		self,
		executable: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		_ = cwd, env
		return f"/usr/bin/{executable}" if executable in self.installed else None

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


def _write_agent(directory: Path, script: str, config: str = "") -> AgentDefinition:
	directory.mkdir(parents=True)
	(directory / "agent.sh").write_text(script, encoding="utf-8")
	if config:
		(directory / "agent.toml").write_text(config, encoding="utf-8")
	return load_agent_dir(directory)


_TOKEN_AGENT = """
reasoning_effort = "high"

[[secrets]]
name = "API_TOKEN"
hint = "export API_TOKEN"
"""


def _token_agent(tmp_path: Path) -> AgentDefinition:
	return _write_agent(tmp_path / "agents" / "token_agent", "true\n", _TOKEN_AGENT)


@pytest.mark.parametrize(
	"error", [OSError("docker unavailable"), subprocess.TimeoutExpired("docker", 5)]
)
def test_timeout_lookup_reports_runtime_failure(
	monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
	runtime = FakeRuntime()

	def fail_lookup(*args: object, **kwargs: object) -> None:
		raise error

	monkeypatch.setattr(runtime, "resolve_executable", fail_lookup)
	with pytest.raises(RuntimeError, match="failed to locate timeout") as caught:
		_timeout_command(runtime, 600)  # type: ignore[arg-type]
	assert caught.value.__cause__ is error


def test_agent_without_prompt_append_gets_the_prompt_unchanged(tmp_path: Path) -> None:
	agent = _write_agent(tmp_path / "plain", "true\n")
	assert _prompt_for_agent(agent, "do the task") == "do the task"


def test_prompt_append_follows_a_blank_line(tmp_path: Path) -> None:
	agent = _write_agent(tmp_path / "append", "true\n", 'prompt_append = "Be brief."\n')
	assert _prompt_for_agent(agent, "do the task\n") == "do the task\n\nBe brief."


def test_docker_agent_runs_as_unprivileged_user() -> None:
	command = _agent_shell_command(DockerRuntime())
	assert command[:4] == ["bash", "-o", "pipefail", "-c"]
	assert command[-1].startswith(
		'runuser --user agent --preserve-environment -- bash "$AEBENCH_AGENT_SUPPORT_DIR"'
		"/aebench/entry.sh </dev/null 2>&1 | "
	)


def test_docker_agent_runtime_matches_host_socket_group(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	commands: list[list[str]] = []

	def run_process(
		_self: DockerRuntime,
		cmd: list[str],
		**_kwargs: object,
	) -> subprocess.CompletedProcess[str]:
		commands.append(cmd)
		return subprocess.CompletedProcess(cmd, 0, "", "")

	monkeypatch.setattr(DockerRuntime, "run_process", run_process)

	prepare_agent_runtime(DockerRuntime())

	assert len(commands) == 1
	assert commands[0][:3] == ["sh", "-e", "-c"]
	assert f"usermod --non-unique --uid {os.getuid()} agent;" in commands[0][3]
	assert 'socket="/var/run/docker.sock"' in commands[0][3]
	assert 'usermod -aG "$socket_group" agent' in commands[0][3]


def test_local_agent_uses_current_user() -> None:
	assert _agent_shell_command(LocalRuntime()) == ["bash", "-o", "pipefail", "-c", _PIPELINE]


def test_runner_passes_harness_contract_and_saves_output(tmp_path: Path) -> None:
	agent = _token_agent(tmp_path)
	support_dir = prepare_agent_support_dir(
		agent, tmp_path / "run", environ={"API_TOKEN": "token-secret"}
	)
	runtime = FakeRuntime()
	output_path = tmp_path / "runner_output.log"

	result = run_agent(
		agent,
		model="model-test",
		prompt="do the task",
		runtime=runtime,  # type: ignore[arg-type]
		cwd="/repo",
		runtime_home="/home/agent",
		host_support_dir=support_dir,
		runtime_support_dir="/run/aebench-agent",
		timeout_seconds=600,
		output_path=output_path,
	)

	assert runtime.command == [
		"timeout",
		"--signal=TERM",
		"--kill-after=30s",
		"600s",
		"bash",
		"-o",
		"pipefail",
		"-c",
		_PIPELINE,
	]
	assert runtime.cwd == "/repo"
	assert runtime.env == {
		"HOME": "/home/agent",
		"AEBENCH_AGENT_SUPPORT_DIR": "/run/aebench-agent",
		"AEBENCH_AGENT_DIR": "/run/aebench-agent/agent",
		"AEBENCH_STATE_DIR": "/run/aebench-agent/state",
		"AEBENCH_PROMPT_FILE": "/run/aebench-agent/aebench/prompt.txt",
		"AEBENCH_MODEL": "model-test",
		"AEBENCH_REASONING_EFFORT": "high",
	}
	# Secrets and the prompt never travel on the `docker exec` command line.
	assert "token-secret" not in " ".join(runtime.command + list(runtime.env.values()))
	assert runtime.stdin_text == ""
	assert (support_dir / "aebench" / "prompt.txt").read_text(encoding="utf-8") == "do the task"
	assert (support_dir / "aebench" / "secrets.sh").read_text(encoding="utf-8") == (
		"export API_TOKEN=token-secret\n"
	)
	assert result.exit_code == 0
	assert result.reasoning_effort == "high"
	assert output_path.read_text(encoding="utf-8") == '{"type":"result"}\n'


def test_runner_fails_before_starting_when_the_harness_is_not_installed(
	tmp_path: Path,
) -> None:
	agent = _write_agent(tmp_path / "needs_cli", "probe-cli\n", 'requires = ["probe-cli"]\n')
	(agent.directory / "install.sh").write_text("true\n", encoding="utf-8")
	runtime = FakeRuntime(installed=("timeout",))
	with pytest.raises(RuntimeError, match="requires probe-cli") as caught:
		check_agent_requirements(agent, runtime)  # type: ignore[arg-type]
	assert str(agent.directory / "install.sh") in str(caught.value)
	assert runtime.command == []


def test_support_dir_holds_agent_copy_and_aebench_files(tmp_path: Path) -> None:
	agent = _token_agent(tmp_path)
	support_dir = prepare_agent_support_dir(
		agent, tmp_path / "run", environ={"API_TOKEN": "token-secret"}
	)

	assert support_dir.stat().st_mode & 0o777 == 0o700
	assert (support_dir / "aebench" / "entry.sh").is_file()
	assert (support_dir / "aebench" / "timestamp_lines.py").is_file()
	assert (support_dir / "agent" / "agent.sh").read_text(encoding="utf-8") == "true\n"
	assert (support_dir / "state").is_dir()
	secrets = support_dir / "aebench" / "secrets.sh"
	assert secrets.stat().st_mode & 0o777 == 0o600


_FILE_AGENT = """
[[files]]
target = ".cli/auth.json"
sources = ["${AUTH_OVERRIDE}", "${CLI_HOME}/auth.json"]
hint = "log in first"
"""


def test_declared_files_are_copied_to_private_state_dir(tmp_path: Path) -> None:
	auth = tmp_path / "auth.json"
	auth.write_text('{"tokens":{}}', encoding="utf-8")
	support_dir = prepare_agent_support_dir(
		_write_agent(tmp_path / "file_agent", "true\n", _FILE_AGENT),
		tmp_path / "run",
		environ={"AUTH_OVERRIDE": str(auth)},
	)

	target = support_dir / "state" / ".cli" / "auth.json"
	assert target.read_text(encoding="utf-8") == '{"tokens":{}}'
	assert target.stat().st_mode & 0o777 == 0o600
	assert not (support_dir / "aebench" / "secrets.sh").exists()


def test_missing_declared_file_does_not_leave_support_dir(tmp_path: Path) -> None:
	parent = tmp_path / "run"
	with pytest.raises(AgentConfigError, match="log in first") as caught:
		prepare_agent_support_dir(
			_write_agent(tmp_path / "file_agent", "true\n", _FILE_AGENT),
			parent,
			environ={"AUTH_OVERRIDE": str(tmp_path / "missing.json")},
		)
	assert "missing.json" in str(caught.value)
	assert not (parent / "agent-support").exists()


def test_missing_secret_names_the_variable_and_hint(tmp_path: Path) -> None:
	with pytest.raises(AgentConfigError, match="missing API_TOKEN: export API_TOKEN"):
		prepare_agent_support_dir(_token_agent(tmp_path), tmp_path / "run", environ={})


def test_docker_agent_environment_does_not_copy_host_runtime(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("API_TOKEN", "token-secret")
	monkeypatch.setenv("PATH", "/host/bin")
	env = _agent_env(
		_token_agent(tmp_path),
		model="model-test",
		runtime_home="/home/agent",
		runtime_support_dir="/run/aebench-agent",
		include_host_runtime=False,
	)
	assert "PATH" not in env
	assert "API_TOKEN" not in env
	assert "PROMPT" not in env


def test_local_agent_runs_through_the_whole_contract(tmp_path: Path) -> None:
	"""A throwaway agent, run for real: environment, secrets, stdin, exit code."""
	agent = _write_agent(
		tmp_path / "agents" / "probe",
		r"""#!/bin/bash
printf 'prompt=%s\n' "$PROMPT"
printf 'secret=%s\n' "$PROBE_TOKEN"
printf 'model=%s effort=%s\n' "$AEBENCH_MODEL" "$AEBENCH_REASONING_EFFORT"
printf 'cwd=%s\n' "$(pwd)"
if read -r line; then echo "stdin=open"; else echo "stdin=empty"; fi
test -f "$AEBENCH_AGENT_SUPPORT_DIR/aebench/secrets.sh" && echo "secrets-file=present" || echo "secrets-file=removed"
cat "$AEBENCH_AGENT_DIR/notes.txt"
echo 'to stderr' >&2
exit 7
""",
		"""
reasoning_effort = "low"
prompt_append = "Be brief."

[[secrets]]
name = "PROBE_TOKEN"
from_env = ["HOST_PROBE_TOKEN"]
""",
	)
	(agent.directory / "notes.txt").write_text("helper file\n", encoding="utf-8")
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	support_dir = prepare_agent_support_dir(
		agent, tmp_path / "run", environ={"HOST_PROBE_TOKEN": 'it\'s $ecret "x"'}
	)
	output_path = tmp_path / "runner_output.log"

	result = run_agent(
		agent,
		model="model-x",
		prompt="line one\nline two",
		runtime=LocalRuntime(workspace=str(workspace)),
		cwd=str(workspace),
		runtime_home=str(tmp_path),
		host_support_dir=support_dir,
		runtime_support_dir=str(support_dir),
		timeout_seconds=30,
		output_path=output_path,
	)

	assert result.exit_code == 7
	assert result.reasoning_effort == "low"
	lines = output_path.read_text(encoding="utf-8").splitlines()
	assert all(re.match(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] ", line) for line in lines)
	text = [line.split("] ", 1)[1] for line in lines]
	assert text[:4] == ["prompt=line one", "line two", "", "Be brief."]
	assert 'secret=it\'s $ecret "x"' in text
	assert "model=model-x effort=low" in text
	assert f"cwd={workspace}" in text
	assert "stdin=empty" in text
	assert "secrets-file=removed" in text
	assert "helper file" in text
	assert "to stderr" in text


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
	support_dir = tmp_path / "agent-support"
	support_dir.mkdir()
	runtime = DockerRuntime(container_name="test-container", resolved_image="aebench-agent:latest")
	session = SimpleNamespace(
		run_spec=task,
		host_workspace=tmp_path,
		runtime_workspace=str(tmp_path),
		host_refs=None,
		host_agent_support_dir=support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
		host_command_socket_dir=None,
		runtime_command_socket_dir=None,
	)

	command = runtime._docker_run_command(session)  # type: ignore[arg-type]
	assert f"{tmp_path}:{tmp_path}" in command
	assert f"{support_dir}:/run/aebench-agent" in command
	assert f"{support_dir}:/home/agent" not in command
	assert command[command.index("-w") + 1] == str(tmp_path)
	assert "/var/run/docker.sock:/var/run/docker.sock" in command


def test_docker_run_mounts_the_command_socket_read_only(tmp_path: Path) -> None:
	task = TaskConfig(
		id="monitored",
		runtime=RuntimeConfig(mode=RuntimeMode.DOCKER, image="aebench-agent:latest"),
	)
	support_dir = tmp_path / "agent-support"
	support_dir.mkdir()
	socket_dir = tmp_path / "sockets" / "mon-abcd1234"
	socket_dir.mkdir(parents=True)
	runtime = DockerRuntime(container_name="test-container", resolved_image="aebench-agent:latest")
	session = SimpleNamespace(
		run_spec=task,
		host_workspace=tmp_path,
		runtime_workspace="/repo",
		host_refs=None,
		host_agent_support_dir=support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
		host_command_socket_dir=socket_dir,
		runtime_command_socket_dir="/run/aebench",
	)

	command = runtime._docker_run_command(session)  # type: ignore[arg-type]

	# Read-only: connect(2) still works, but the agent -- which has sudo -- cannot
	# unlink the socket and quietly end its own monitoring.
	assert f"{socket_dir}:/run/aebench:ro" in command
	# The socket directory is the only monitoring path the container can see; the
	# trace itself is never mounted.
	assert not any("commands.jsonl" in argument for argument in command)


def test_docker_run_has_no_socket_mount_without_monitoring(tmp_path: Path) -> None:
	task = TaskConfig(
		id="unmonitored",
		runtime=RuntimeConfig(mode=RuntimeMode.DOCKER, image="aebench-agent:latest"),
	)
	support_dir = tmp_path / "agent-support"
	support_dir.mkdir()
	runtime = DockerRuntime(container_name="test-container", resolved_image="aebench-agent:latest")
	session = SimpleNamespace(
		run_spec=task,
		host_workspace=tmp_path,
		runtime_workspace="/repo",
		host_refs=None,
		host_agent_support_dir=support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
		host_command_socket_dir=None,
		runtime_command_socket_dir=None,
	)

	assert not any("/run/aebench:" in argument for argument in runtime._docker_run_command(session))  # type: ignore[arg-type]


def test_agent_shell_command_can_bypass_the_shim() -> None:
	"""Under monitoring the harness runs on the preserved real bash.

	Its outer shell carries the agent's whole transcript through a pipe, and the
	shim buffers a command's output until the command ends, so routing it through
	the shim would hold the entire run's output in container RAM -- besides
	recording AEBench's own two shells as if the agent had run them.
	"""
	real_shell = "/usr/lib/aebench/bash.real"

	command = _agent_shell_command(DockerRuntime(), shell_path=real_shell)

	assert command[0] == real_shell
	# Both the outer pipeline shell and the inner one the entry script runs on...
	assert f'-- {real_shell} "$AEBENCH_AGENT_SUPPORT_DIR"/aebench/entry.sh' in command[-1]
	assert "-- bash " not in command[-1]
	# ...which runs agent.sh on the same shell, not on /bin/bash (the shim).
	entry = (Path(agent_runner.__file__).with_name("agent_support") / "entry.sh").read_text()
	assert 'exec "$BASH" "$AEBENCH_AGENT_DIR/agent.sh"' in entry


def test_agent_shell_command_defaults_to_plain_bash() -> None:
	# An unmonitored run must be byte-identical to before.
	command = _agent_shell_command(DockerRuntime())

	assert command[0] == "bash"
	assert '-- bash "$AEBENCH_AGENT_SUPPORT_DIR"/aebench/entry.sh' in command[-1]


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
		run_spec=TaskConfig(
			id="test",
			runtime=RuntimeConfig(
				mode="docker", keep_committed_snapshot=False, include_workspace_in_snapshot=False
			),
		),
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
