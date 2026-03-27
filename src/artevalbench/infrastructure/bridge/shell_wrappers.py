from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

from ...models import AgentSessionContext

SESSION_FILE_ENV = "ARTEVALBENCH_AGENT_SESSION_FILE"
HOST_PATH_ENV = "ARTEVALBENCH_HOST_PATH"
REAL_BASH_ENV = "ARTEVALBENCH_REAL_BASH"
REAL_SH_ENV = "ARTEVALBENCH_REAL_SH"
REAL_DOCKER_ENV = "ARTEVALBENCH_REAL_DOCKER"
CONTAINER_WRAPPER_NAME = "artevalbench-container-bash"
HOST_WRAPPER_NAME = "artevalbench-host-bash"


def ensure_host_shell_wrappers(
 *,
 bin_dir: Path,
 python_executable: str,
 shim_shells: bool = False,
 expose_container_shell: bool = False,
 expose_host_shell: bool = False,
) -> Path:
	bin_dir.mkdir(parents=True, exist_ok=True)
	wrappers: list[tuple[str, str, str]] = []
	if shim_shells:
		wrappers.extend(
		 [
		  ("bash", "container", "bash"),
		  ("sh", "container", "sh"),
		 ]
		)
	if expose_container_shell:
		wrappers.append((CONTAINER_WRAPPER_NAME, "container", "sh"))
	if expose_host_shell:
		wrappers.append((HOST_WRAPPER_NAME, "host", "bash"))
	for wrapper_name, mode, shell_name in wrappers:
		script_path = bin_dir / wrapper_name
		script_path.write_text(
		 _wrapper_script(
		  python_executable=python_executable,
		  mode=mode,
		  shell_name=shell_name,
		 ),
		 encoding="utf-8",
		)
		script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
	return bin_dir


def wrapper_main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(add_help=False)
	parser.add_argument("--mode", choices=("container", "host"), required=True)
	parser.add_argument("--shell-name", default="sh")
	parser.add_argument("shell_args", nargs=argparse.REMAINDER)
	args = parser.parse_args(argv)
	shell_args = list(args.shell_args)
	if shell_args[:1] == ["--"]:
		shell_args = shell_args[1:]
	if args.mode == "host":
		_exec_host_shell(args.shell_name, shell_args)
		return 0
	_exec_container_shell(args.shell_name, shell_args)
	return 0


def _wrapper_script(*, python_executable: str, mode: str, shell_name: str) -> str:
	return (
	 "#!/bin/sh\n"
	 f'exec "{python_executable}" '
	 "-m artevalbench.infrastructure.bridge.shell_wrappers "
	 f'--mode "{mode}" --shell-name "{shell_name}" -- "$@"\n'
	)


def _exec_host_shell(shell_name: str, shell_args: list[str]) -> None:
	real_shell = os.environ.get(_real_shell_env(shell_name))
	if not real_shell:
		real_shell = shutil.which(shell_name)
	if not real_shell:
		raise SystemExit(f"missing real host shell for wrapper: {shell_name}")
	env = os.environ.copy()
	host_path = env.get(HOST_PATH_ENV)
	if host_path is not None:
		env["PATH"] = host_path
	os.execve(real_shell, [real_shell, *shell_args], env)


def _exec_container_shell(shell_name: str, shell_args: list[str]) -> None:
	_ = shell_name
	session = _load_session()
	container_id = os.environ.get("ARTEVALBENCH_CONTAINER_ID")
	if not container_id:
		raise SystemExit("ARTEVALBENCH_CONTAINER_ID is not set")
	docker_binary = os.environ.get(REAL_DOCKER_ENV)
	if not docker_binary:
		docker_binary = shutil.which("docker")
	if not docker_binary:
		raise SystemExit("missing host docker binary for wrapper")
	cwd = _container_cwd(session)
	docker_argv = ["docker", "exec"]
	if not shell_args and _stdin_is_tty():
		docker_argv.append("-it")
	if cwd:
		docker_argv.extend(["-w", cwd])
	docker_argv.extend([container_id, "sh", *_translate_shell_args(shell_args, session)])
	os.execve(docker_binary, [docker_binary, *docker_argv[1:]], os.environ.copy())


def _load_session() -> AgentSessionContext:
	session_file = os.environ.get(SESSION_FILE_ENV)
	if not session_file:
		raise SystemExit(f"{SESSION_FILE_ENV} is not set")
	path = Path(session_file)
	if not path.is_file():
		raise SystemExit(f"session file not found: {path}")
	return AgentSessionContext.model_validate_json(path.read_text(encoding="utf-8"))


def _container_cwd(session: AgentSessionContext) -> str | None:
	host_workspace = session.host_workspace_path
	container_workspace = session.container_workspace_path or session.workspace_path
	try:
		current = Path.cwd().resolve()
	except OSError:
		return container_workspace
	if not host_workspace or not container_workspace:
		return container_workspace
	host_root = Path(host_workspace).resolve()
	try:
		relative = current.relative_to(host_root)
	except ValueError:
		return container_workspace
	return str((Path(container_workspace) / relative).as_posix())


def _translate_shell_args(
 shell_args: list[str],
 session: AgentSessionContext,
) -> list[str]:
	if not shell_args:
		return ["sh"]
	host_workspace = session.host_workspace_path
	container_workspace = session.container_workspace_path or session.workspace_path
	if not host_workspace or not container_workspace:
		return list(shell_args)
	host_root = Path(host_workspace).resolve()
	translated: list[str] = []
	for arg in shell_args:
		if arg.startswith("-"):
			translated.append(arg)
			continue
		translated.append(_translate_host_path(arg, host_root, container_workspace))
	return translated


def _translate_host_path(arg: str, host_root: Path, container_workspace: str) -> str:
	candidate = Path(arg)
	if not candidate.is_absolute():
		return arg
	try:
		relative = candidate.resolve().relative_to(host_root)
	except (ValueError, OSError):
		return arg
	return str((Path(container_workspace) / relative).as_posix())


def _real_shell_env(shell_name: str) -> str:
	return REAL_BASH_ENV if shell_name == "bash" else REAL_SH_ENV


def _stdin_is_tty() -> bool:
	return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


if __name__ == "__main__":
	raise SystemExit(wrapper_main())
