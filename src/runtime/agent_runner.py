"""Run agent harnesses inside an AEBench runtime.

Nothing here is specific to one agent: everything an agent needs is declared in
its directory (``agent.sh`` and ``agent.toml``, see runtime.agents).

The per-run support directory, mounted at ``$AEBENCH_AGENT_SUPPORT_DIR``:

	aebench/    entry script, output timestamper, prompt, secrets (AEBench-owned)
	agent/      a copy of the agent directory ($AEBENCH_AGENT_DIR)
	state/      the harness's config and sessions ($AEBENCH_STATE_DIR)
	artifacts/  files saved to <run>/agent-artifacts after the run ($AEBENCH_ARTIFACTS_DIR)

It is deleted after the run.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from models import AgentResult

from .agents import AgentDefinition, resolve_files, resolve_secrets
from .backend import BenchRuntime, DockerRuntime, LocalRuntime

logger = logging.getLogger(__name__)

_RUNTIME_ENV_KEYS = ("PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_RUNTIME_SETUP_TIMEOUT_SECONDS = 30
_REQUIREMENT_CHECK_TIMEOUT_SECONDS = 30
_AGENT_TERMINATION_GRACE_SECONDS = 30
_HOST_PROCESS_GRACE_SECONDS = 5
_DOCKER_HOST_SOCKET_SETUP = (
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

_SUPPORT_FILES_DIR = Path(__file__).with_name("agent_support")
AEBENCH_SUBDIR = "aebench"
AGENT_SUBDIR = "agent"
STATE_SUBDIR = "state"
ARTIFACTS_SUBDIR = "artifacts"
ENTRY_SCRIPT = "entry.sh"
TIMESTAMP_SCRIPT = "timestamp_lines.py"
PROMPT_FILE = "prompt.txt"
SECRETS_FILE = "secrets.sh"
AGENT_ARTIFACTS_DIRNAME = "agent-artifacts"
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git")


def prepare_agent_support_dir(
	agent: AgentDefinition,
	parent: Path,
	*,
	environ: Mapping[str, str] | None = None,
) -> Path:
	"""Creates the private per-run support directory, with the agent's credentials."""
	support_dir = parent / "agent-support"
	support_dir.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True)
	try:
		# Resolve first, so a missing credential fails before anything is copied.
		secrets = resolve_secrets(agent, environ=environ)
		files = resolve_files(agent, environ=environ)

		aebench_dir = support_dir / AEBENCH_SUBDIR
		aebench_dir.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
		for name in (ENTRY_SCRIPT, TIMESTAMP_SCRIPT):
			shutil.copyfile(_SUPPORT_FILES_DIR / name, aebench_dir / name)
		if secrets:
			_write_private(aebench_dir / SECRETS_FILE, _secrets_script(secrets))

		shutil.copytree(agent.directory, support_dir / AGENT_SUBDIR, ignore=_COPY_IGNORE)

		state_dir = support_dir / STATE_SUBDIR
		state_dir.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
		for source, target in files:
			destination = state_dir / target
			destination.parent.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
			shutil.copyfile(source, destination)
			destination.chmod(_PRIVATE_FILE_MODE)

		(support_dir / ARTIFACTS_SUBDIR).mkdir(mode=_PRIVATE_DIRECTORY_MODE)
	except BaseException:
		shutil.rmtree(support_dir, ignore_errors=True)
		raise

	return support_dir


def run_agent(
	agent: AgentDefinition,
	*,
	model: str,
	prompt: str,
	runtime: BenchRuntime,
	cwd: str,
	runtime_home: str,
	host_support_dir: Path,
	runtime_support_dir: str,
	timeout_seconds: float,
	output_path: Path,
	shell_path: str = "bash",
) -> AgentResult:
	reasoning_effort = agent.manifest.reasoning_effort
	check_agent_requirements(agent, runtime)
	_write_private(
		host_support_dir / AEBENCH_SUBDIR / PROMPT_FILE, _prompt_for_agent(agent, prompt)
	)
	env = _agent_env(
		agent,
		model=model,
		cwd=cwd,
		runtime_home=runtime_home,
		runtime_support_dir=runtime_support_dir,
		timeout_seconds=timeout_seconds,
		include_host_runtime=isinstance(runtime, LocalRuntime),
	)
	command = _timeout_command(runtime, timeout_seconds) + _agent_shell_command(
		runtime, shell_path=shell_path
	)

	try:
		process = runtime.run_process_to_file(
			command,
			output_path=output_path,
			cwd=cwd,
			env=env,
			# Closes stdin; the agent never reads from the terminal that started AEBench.
			stdin_text="",
			timeout=(
				timeout_seconds + _AGENT_TERMINATION_GRACE_SECONDS + _HOST_PROCESS_GRACE_SECONDS
			),
		)
	except subprocess.TimeoutExpired:
		return AgentResult(model=model, exit_code=124, reasoning_effort=reasoning_effort)
	return AgentResult(
		model=model,
		exit_code=process.returncode,
		reasoning_effort=reasoning_effort,
	)


def check_agent_requirements(agent: AgentDefinition, runtime: BenchRuntime) -> None:
	"""Fails before the agent starts when an executable it requires is not installed."""
	missing: list[str] = []
	for executable in agent.manifest.requires:
		try:
			resolved = runtime.resolve_executable(executable)
		except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
			raise RuntimeError(
				f"failed to look up {executable!r} for agent {agent.name!r}: {exc}"
			) from exc
		if resolved is None:
			missing.append(executable)
	if not missing:
		return
	where = (
		"the runtime image; rebuild it (docker build -t aebench-agent:latest .)"
		if isinstance(runtime, DockerRuntime)
		else "this machine's PATH"
	)
	install = agent.install_script
	hint = f"; see {install}" if install is not None else ""
	raise RuntimeError(
		f"agent {agent.name!r} requires {', '.join(missing)}, which is not installed in "
		f"{where}{hint}"
	)


def prepare_agent_runtime(runtime: BenchRuntime) -> None:
	if not isinstance(runtime, DockerRuntime):
		return

	result = runtime.run_process(
		[
			"sh",
			"-e",
			"-c",
			f"usermod --non-unique --uid {os.getuid()} agent; {_DOCKER_HOST_SOCKET_SETUP}",
		],
		timeout=_RUNTIME_SETUP_TIMEOUT_SECONDS,
	)
	if result.returncode != 0:
		detail = (result.stderr or result.stdout).strip()
		raise RuntimeError(
			f"failed to prepare the Docker agent user: {detail or result.returncode}"
		)


def collect_agent_artifacts(host_support_dir: Path, output_dir: Path) -> Path | None:
	"""Copies $AEBENCH_ARTIFACTS_DIR into the run's output directory.

	Runs after the container stops. Reject a linked source directory and preserve
	links inside it without following them.
	"""
	source = host_support_dir / ARTIFACTS_SUBDIR
	try:
		if source.is_symlink() or not source.is_dir() or not any(source.iterdir()):
			return None
	except OSError:
		logger.warning("cannot read the agent artifacts directory %s", source, exc_info=True)
		return None
	destination = output_dir / AGENT_ARTIFACTS_DIRNAME
	try:
		shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
	except (OSError, shutil.Error):
		logger.warning("some agent artifacts could not be saved to %s", destination, exc_info=True)
	return destination


def clear_agent_support_dir(runtime: BenchRuntime, runtime_support_dir: str) -> None:
	result = runtime.run_process(
		[
			"sh",
			"-c",
			'test ! -d "$1" || find "$1" -mindepth 1 -maxdepth 1 ! -name artifacts -exec rm -rf -- {} +',
			"aebench-clear-support",
			runtime_support_dir,
		],
		timeout=_RUNTIME_SETUP_TIMEOUT_SECONDS,
	)
	if result.returncode != 0:
		detail = (result.stderr or result.stdout).strip()
		raise RuntimeError(
			f"failed to clear the temporary agent support directory: {detail or result.returncode}"
		)


def _prompt_for_agent(agent: AgentDefinition, prompt: str) -> str:
	append = (agent.manifest.prompt_append or "").strip()
	if not append:
		return prompt
	return f"{prompt.rstrip()}\n\n{append}"


def _agent_env(
	agent: AgentDefinition,
	*,
	model: str,
	cwd: str,
	runtime_home: str,
	runtime_support_dir: str,
	timeout_seconds: float,
	include_host_runtime: bool,
) -> dict[str, str]:
	"""The agent's environment, minus secrets and the prompt.

	Docker passes these as ``docker exec -e`` arguments, which other host users
	can read, so secrets and the prompt travel as files in the support directory.
	"""
	env = (
		{key: os.environ[key] for key in _RUNTIME_ENV_KEYS if key in os.environ}
		if include_host_runtime
		else {}
	)
	support = runtime_support_dir.rstrip("/")
	env.update(
		{
			"HOME": runtime_home,
			"AEBENCH_AGENT_NAME": agent.name,
			"AEBENCH_AGENT_SUPPORT_DIR": support,
			"AEBENCH_AGENT_DIR": f"{support}/{AGENT_SUBDIR}",
			"AEBENCH_STATE_DIR": f"{support}/{STATE_SUBDIR}",
			"AEBENCH_ARTIFACTS_DIR": f"{support}/{ARTIFACTS_SUBDIR}",
			"AEBENCH_PROMPT_FILE": f"{support}/{AEBENCH_SUBDIR}/{PROMPT_FILE}",
			"AEBENCH_WORKSPACE": cwd,
			"AEBENCH_TIMEOUT_SECONDS": str(int(timeout_seconds)),
			"AEBENCH_MODEL": model,
			# The original name of AEBENCH_MODEL, kept for existing scripts.
			"AGENT_CONFIG": model,
		}
	)
	if agent.manifest.reasoning_effort is not None:
		env["AEBENCH_REASONING_EFFORT"] = agent.manifest.reasoning_effort
	return env


def _secrets_script(secrets: Mapping[str, str]) -> str:
	return "".join(f"export {name}={shlex.quote(value)}\n" for name, value in secrets.items())


def _write_private(path: Path, text: str) -> None:
	descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _PRIVATE_FILE_MODE)
	with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
		handle.write(text)
	path.chmod(_PRIVATE_FILE_MODE)


def _timeout_command(runtime: BenchRuntime, timeout_seconds: float) -> list[str]:
	for executable in ("timeout", "gtimeout"):
		try:
			resolved = runtime.resolve_executable(executable)
		except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
			raise RuntimeError(
				f"failed to locate {executable} in the agent runtime: {exc}"
			) from exc
		if resolved is not None:
			return [
				executable,
				"--signal=TERM",
				f"--kill-after={_AGENT_TERMINATION_GRACE_SECONDS}s",
				f"{timeout_seconds:g}s",
			]
	raise RuntimeError("agent runtime requires GNU timeout (timeout or gtimeout)")


def _agent_shell_command(runtime: BenchRuntime, *, shell_path: str = "bash") -> list[str]:
	if isinstance(runtime, DockerRuntime):
		prefix = ["runuser", "--user", "agent", "--preserve-environment", "--", shell_path]
	else:
		prefix = [shell_path]
	support = '"$AEBENCH_AGENT_SUPPORT_DIR"'
	pipeline = (
		f"{shlex.join(prefix)} {support}/{AEBENCH_SUBDIR}/{ENTRY_SCRIPT} </dev/null 2>&1 | "
		f"python3 {support}/{AEBENCH_SUBDIR}/{TIMESTAMP_SCRIPT}"
	)
	return [shell_path, "-o", "pipefail", "-c", pipeline]


__all__ = [
	"check_agent_requirements",
	"clear_agent_support_dir",
	"collect_agent_artifacts",
	"prepare_agent_runtime",
	"prepare_agent_support_dir",
	"run_agent",
]
