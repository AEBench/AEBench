"""Run shell agent harnesses inside an AEBench runtime."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

from models import AgentName, AgentResult

from .backend import BenchRuntime, DockerRuntime, LocalRuntime

_RUNTIME_ENV_KEYS = ("PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
_CLAUDE_NONINTERACTIVE_GUIDANCE = (
	"You are running in a non-interactive mode. So make sure every process you are "
	"running finishes before you write your last message."
)
_DOCKER_AGENT_USER_SETUP = (
	'socket="/var/run/docker.sock"; '
	'if [ -S "$socket" ]; then '
	'socket_gid=$(stat -c %g "$socket"); '
	'socket_group=$(getent group "$socket_gid" | cut -d: -f1 || true); '
	'if [ -z "$socket_group" ]; then '
	'socket_group=aebench-docker-host; groupadd --gid "$socket_gid" "$socket_group"; '
	"fi; "
	'usermod -aG "$socket_group" agent; '
	"fi"
)
_TIMESTAMP_SCRIPT = "timestamp_lines.py"
_REASONING_EFFORT: dict[AgentName, str] = {
	"codex": "high",
	"codex_non_api": "high",
	"claude_non_api": "high",
}


def prepare_agent_home(
	agent: AgentName,
	parent: Path,
	*,
	environ: Mapping[str, str] | None = None,
) -> Path:
	env = os.environ if environ is None else environ
	home = parent / "agent-home"
	home.mkdir(mode=0o700, parents=True)
	try:
		shutil.copyfile(
			Path(__file__).with_name("agent_scripts") / _TIMESTAMP_SCRIPT,
			home / _TIMESTAMP_SCRIPT,
		)
		if agent == "codex_non_api":
			default_codex_home = Path(env.get("CODEX_HOME", "~/.codex")).expanduser()
			source = Path(
				env.get("AEBENCH_CODEX_AUTH_FILE", str(default_codex_home / "auth.json"))
			).expanduser()
			if not source.is_file():
				raise RuntimeError(
					"Codex subscription auth not found; run `codex login` or set "
					"AEBENCH_CODEX_AUTH_FILE"
				)
			target = home / ".codex" / "auth.json"
			target.parent.mkdir(mode=0o700)
			shutil.copyfile(source, target)
			target.chmod(0o600)

		elif agent == "claude_non_api":
			token = env.get("CLAUDE_CODE_OAUTH_TOKEN")
			if token is None:
				token_path = env.get("AEBENCH_CLAUDE_OAUTH_TOKEN_FILE")
				if token_path:
					token = Path(token_path).expanduser().read_text(encoding="utf-8").strip()
			if not token:
				raise RuntimeError(
					"Claude subscription auth not found; set CLAUDE_CODE_OAUTH_TOKEN or "
					"AEBENCH_CLAUDE_OAUTH_TOKEN_FILE"
				)
			target = home / "oauth_token"
			target.write_text(token, encoding="utf-8")
			target.chmod(0o600)
	except Exception:
		shutil.rmtree(home, ignore_errors=True)
		raise

	return home


def run_agent(
	agent: AgentName,
	*,
	model: str,
	prompt: str,
	runtime: BenchRuntime,
	cwd: str,
	runtime_home: str,
	timeout_seconds: float,
	output_path: Path,
) -> AgentResult:
	script = _solve_script(agent)
	prompt = _prompt_for_agent(agent, prompt)
	reasoning_effort = _REASONING_EFFORT.get(agent)
	env = _agent_env(
		agent,
		model=model,
		prompt=prompt,
		runtime_home=runtime_home,
		include_host_runtime=isinstance(runtime, LocalRuntime),
	)
	command = _timeout_command(runtime, timeout_seconds) + _agent_shell_command(runtime)

	try:
		process = runtime.run_process_to_file(
			command,
			output_path=output_path,
			cwd=cwd,
			env=env,
			stdin_text=script,
			timeout=timeout_seconds + 35,
		)
	except subprocess.TimeoutExpired:
		return AgentResult(model=model, exit_code=124, reasoning_effort=reasoning_effort)
	return AgentResult(
		model=model,
		exit_code=process.returncode,
		reasoning_effort=reasoning_effort,
	)


def _solve_script(agent: AgentName) -> str:
	return (Path(__file__).with_name("agent_scripts") / agent / "solve.sh").read_text(
		encoding="utf-8"
	)


def _prompt_for_agent(agent: AgentName, prompt: str) -> str:
	if agent not in {"claude", "claude_non_api"}:
		return prompt
	return f"{prompt.rstrip()}\n\n{_CLAUDE_NONINTERACTIVE_GUIDANCE}"


def clear_agent_home(runtime: BenchRuntime, runtime_home: str) -> None:
	result = runtime.run_process(
		[
			"sh",
			"-c",
			'test ! -d "$1" || find "$1" -mindepth 1 -delete',
			"aebench-clear-home",
			runtime_home,
		],
		timeout=30,
	)
	if result.returncode != 0:
		detail = (result.stderr or result.stdout).strip()
		raise RuntimeError(f"failed to clear per-run agent home: {detail or result.returncode}")


def _agent_env(
	agent: AgentName,
	*,
	model: str,
	prompt: str,
	runtime_home: str,
	include_host_runtime: bool,
) -> dict[str, str]:
	env = (
		{key: os.environ[key] for key in _RUNTIME_ENV_KEYS if key in os.environ}
		if include_host_runtime
		else {}
	)
	env["HOME"] = runtime_home
	reasoning_effort = _REASONING_EFFORT.get(agent)
	if reasoning_effort is not None:
		env["AEBENCH_REASONING_EFFORT"] = reasoning_effort
	if agent == "codex":
		key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY")
		if not key:
			raise RuntimeError("Codex API harness requires CODEX_API_KEY or OPENAI_API_KEY")
		env["CODEX_API_KEY"] = key
	elif agent == "claude":
		key = os.environ.get("ANTHROPIC_API_KEY")
		if not key:
			raise RuntimeError("Claude API harness requires ANTHROPIC_API_KEY")
		env["ANTHROPIC_API_KEY"] = key
	env["PROMPT"] = prompt
	env["AGENT_CONFIG"] = model
	return env


def _timeout_command(runtime: BenchRuntime, timeout_seconds: float) -> list[str]:
	for executable in ("timeout", "gtimeout"):
		if runtime.resolve_executable(executable) is not None:
			return [
				executable,
				"--signal=TERM",
				"--kill-after=30s",
				f"{timeout_seconds:g}s",
			]
	raise RuntimeError("agent runtime requires GNU timeout (timeout or gtimeout)")


def _agent_shell_command(runtime: BenchRuntime) -> list[str]:
	if isinstance(runtime, DockerRuntime):
		agent_command = [
			"runuser",
			"--user",
			"agent",
			"--preserve-environment",
			"--",
			"bash",
			"-s",
		]
		user_setup = f"set -e; {_DOCKER_AGENT_USER_SETUP}; "
	else:
		agent_command = ["bash", "-s"]
		user_setup = ""
	pipeline = f'{user_setup}{shlex.join(agent_command)} 2>&1 | python3 "$HOME/{_TIMESTAMP_SCRIPT}"'
	return ["bash", "-o", "pipefail", "-c", pipeline]


__all__ = ["AgentName", "clear_agent_home", "prepare_agent_home", "run_agent"]
