"""Local and Docker runtime backends."""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from constants import DEFAULT_ORACLE_CHECK_TIMEOUT
from models import RuntimeInfo, RuntimeMode
from utils import safe_name

if TYPE_CHECKING:
	from .session import RunSession

logger = logging.getLogger(__name__)

_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_env_var_name(name: str) -> str:
	if not _ENV_VAR_NAME_RE.fullmatch(name):
		raise ValueError(f"invalid environment variable name: {name!r}")
	return name


class BenchRuntime(Protocol):
	@property
	def path_separator(self) -> str: ...

	def prepare(self, session: RunSession) -> None: ...

	def run_process(
		self,
		cmd: list[str],
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float = DEFAULT_ORACLE_CHECK_TIMEOUT,
	) -> subprocess.CompletedProcess[str]: ...

	def run_process_to_file(
		self,
		cmd: list[str],
		*,
		output_path: Path,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float = DEFAULT_ORACLE_CHECK_TIMEOUT,
	) -> subprocess.CompletedProcess[str]: ...

	def open_shell(
		self, *, cwd: str | None = None, env: Mapping[str, str] | None = None
	) -> int: ...

	def resolve_executable(
		self,
		executable: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None: ...

	def read_env_var(
		self,
		name: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None: ...

	def snapshot(self, session: RunSession) -> str | None: ...

	def stop(self, session: RunSession) -> None: ...

	def cleanup(self, session: RunSession) -> None: ...

	def runtime_result(self, session: RunSession) -> RuntimeInfo: ...


def _merged_env(env: Mapping[str, str] | None) -> dict[str, str]:
	merged = dict(os.environ)
	if env:
		merged.update(env)
	return merged


def _env_flags(env: Mapping[str, str] | None) -> list[str]:
	if not env:
		return []
	flags: list[str] = []
	for key, value in env.items():
		flags.extend(["-e", f"{validate_env_var_name(key)}={value}"])
	return flags


@dataclass(slots=True)
class DockerRuntime:
	image: str | None = None
	gpu: bool = False

	container_id: str | None = None
	container_name: str | None = None
	resolved_image: str | None = None
	last_container_id: str | None = None
	container_removed: bool = False
	container_stopped: bool = False
	saved_image: str | None = None
	path_separator: str = ":"

	def _docker_run_command(self, session: RunSession) -> list[str]:
		assert self.container_name is not None
		assert self.resolved_image is not None
		cmd = [
			"docker",
			"run",
			"-d",
			"--init",
			"--name",
			self.container_name,
			"-v",
			f"{session.host_workspace}:{session.runtime_workspace}",
			"-v",
			f"{session.host_agent_support_dir}:{session.runtime_agent_support_dir}",
			"-w",
			session.runtime_workspace,
		]
		if session.host_refs is not None:
			cmd.extend(["-v", f"{session.host_refs}:/refs:ro"])
		if session.run_spec.artifact_requirements.docker:
			cmd.extend(["-v", "/var/run/docker.sock:/var/run/docker.sock"])
		if self.gpu or bool(getattr(session.run_spec.runtime, "gpu", False)):
			cmd.extend(["--gpus", "all"])
		cmd.extend([self.resolved_image, "sleep", "infinity"])
		return cmd

	def prepare(self, session: RunSession) -> None:
		image = (
			self.image
			or session.run_spec.runtime.image
			or getattr(session.settings, "default_docker_image", None)
		)
		if not image:
			raise RuntimeError("docker runtime requires an image")

		self.resolved_image = image
		self.last_container_id = None
		self.container_removed = False
		self.container_stopped = False
		self.saved_image = None
		self.container_name = f"aebench-{safe_name(session.task_id)}-{uuid.uuid4().hex[:8]}"

		cmd = self._docker_run_command(session)

		result = subprocess.run(cmd, capture_output=True, text=True, check=False)
		if result.returncode != 0:
			self.container_name = None
			raise RuntimeError(f"docker run failed: {(result.stderr or result.stdout).strip()}")

		self.container_id = result.stdout.strip()
		logger.info("started docker container %s (%s)", self.container_id, self.container_name)

	def run_process(
		self,
		cmd: list[str],
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float | None = None,
	) -> subprocess.CompletedProcess[str]:
		if self.container_id is None:
			raise RuntimeError("docker runtime is not prepared")

		docker_cmd = ["docker", "exec"]
		if stdin_text is not None:
			docker_cmd.append("-i")
		if cwd:
			docker_cmd.extend(["-w", cwd])
		docker_cmd.extend(_env_flags(env))
		docker_cmd.append(self.container_id)
		docker_cmd.extend(cmd)

		return subprocess.run(
			docker_cmd,
			input=stdin_text,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=False,
		)

	def run_process_to_file(
		self,
		cmd: list[str],
		*,
		output_path: Path,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float | None = None,
	) -> subprocess.CompletedProcess[str]:
		if self.container_id is None:
			raise RuntimeError("docker runtime is not prepared")
		docker_cmd = ["docker", "exec", "-i"]
		if cwd:
			docker_cmd.extend(["-w", cwd])
		docker_cmd.extend(_env_flags(env))
		docker_cmd.append(self.container_id)
		docker_cmd.extend(cmd)
		return _run_to_file(
			docker_cmd,
			output_path=output_path,
			stdin_text=stdin_text,
			timeout=timeout,
		)

	def open_shell(self, *, cwd: str | None = None, env: Mapping[str, str] | None = None) -> int:
		if self.container_id is None:
			raise RuntimeError("docker runtime is not prepared")

		shell_check = self.run_process(["sh", "-lc", "command -v bash >/dev/null 2>&1"], timeout=5)
		shell = "bash" if shell_check.returncode == 0 else "sh"

		cmd = ["docker", "exec", "-it"]
		if cwd:
			cmd.extend(["-w", cwd])
		cmd.extend(_env_flags(env))
		cmd.extend([self.container_id, shell])
		return subprocess.call(cmd)

	def resolve_executable(
		self,
		executable: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		result = self.run_process(
			["sh", "-lc", f"command -v {shlex.quote(executable)}"],
			cwd=cwd,
			env=env,
			timeout=5.0,
		)
		if result.returncode != 0:
			return None
		resolved = result.stdout.strip()
		return resolved or None

	def read_env_var(
		self,
		name: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		result = self.run_process(
			["printenv", validate_env_var_name(name)],
			cwd=cwd,
			env=env,
			timeout=5.0,
		)
		if result.returncode != 0:
			return None
		return result.stdout.removesuffix("\n")

	def snapshot(self, session: RunSession) -> str | None:
		if self.container_id is None:
			return self.saved_image

		tag = f"aebench-oracle-snapshots:{safe_name(session.task_id)}-{uuid.uuid4().hex[:8]}"
		result = subprocess.run(
			["docker", "commit", self.container_id, tag],
			capture_output=True,
			text=True,
			timeout=session.run_spec.runtime.snapshot_timeout_seconds,
			check=False,
		)
		if result.returncode != 0:
			raise RuntimeError(
				f"docker commit failed: {(result.stderr or result.stdout).strip() or tag}"
			)

		self.saved_image = tag
		logger.info("committed docker container %s to %s", self.container_id, tag)
		return tag

	def stop(self, session: RunSession) -> None:
		_ = session
		if self.container_id is None or self.container_stopped:
			return
		result = subprocess.run(
			["docker", "stop", "--time", "30", self.container_id],
			capture_output=True,
			text=True,
			check=False,
		)
		if result.returncode != 0:
			raise RuntimeError(
				"failed to stop docker container "
				f"{self.container_id}: {(result.stderr or result.stdout).strip()}"
			)
		self.last_container_id = self.container_id
		self.container_stopped = True

	def cleanup(self, session: RunSession) -> None:
		target = self.container_id or self.container_name
		if target:
			if self.container_id is not None:
				self.last_container_id = self.container_id
			result = subprocess.run(
				["docker", "rm", "-f", target], capture_output=True, text=True, check=False
			)
			if result.returncode != 0:
				raise RuntimeError(
					"failed to remove docker container "
					f"{target}: {(result.stderr or result.stdout).strip()}"
				)
			self.container_id = None
			self.container_name = None
			self.container_removed = True
			self.container_stopped = True

		if self.saved_image and not session.run_spec.runtime.keep_committed_snapshot:
			image = self.saved_image
			result = subprocess.run(
				["docker", "rmi", "-f", image], capture_output=True, text=True, check=False
			)
			if result.returncode == 0:
				self.saved_image = None
			else:
				logger.warning(
					"failed to remove docker image %s: %s",
					image,
					(result.stderr or result.stdout).strip(),
				)

	def runtime_result(self, session: RunSession) -> RuntimeInfo:
		return RuntimeInfo(
			mode=RuntimeMode.DOCKER,
			image=self.resolved_image or self.image,
			workspace_mount=session.runtime_workspace,
			user=session.runtime_agent_user,
			home=session.runtime_agent_home,
			container_id=self.container_id or self.last_container_id,
			saved_image=self.saved_image,
			container_stopped=self.container_stopped or self.container_removed,
		)


@dataclass(slots=True)
class LocalRuntime:
	workspace: str | None = None
	path_separator: str = os.pathsep

	def prepare(self, session: RunSession) -> None:
		self.workspace = str(session.host_workspace)

	def run_process(
		self,
		cmd: list[str],
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float | None = None,
	) -> subprocess.CompletedProcess[str]:
		return subprocess.run(
			cmd,
			input=stdin_text,
			capture_output=True,
			text=True,
			cwd=cwd or self.workspace,
			env=_merged_env(env),
			timeout=timeout,
			check=False,
		)

	def run_process_to_file(
		self,
		cmd: list[str],
		*,
		output_path: Path,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float | None = None,
	) -> subprocess.CompletedProcess[str]:
		return _run_to_file(
			cmd,
			output_path=output_path,
			cwd=cwd or self.workspace,
			env=dict(env or {}),
			stdin_text=stdin_text,
			timeout=timeout,
		)

	def open_shell(self, *, cwd: str | None = None, env: Mapping[str, str] | None = None) -> int:
		return subprocess.call(
			[os.environ.get("SHELL", "bash")], cwd=cwd or self.workspace, env=_merged_env(env)
		)

	def resolve_executable(
		self,
		executable: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		_ = cwd
		return shutil.which(executable, path=None if env is None else env.get("PATH"))

	def read_env_var(
		self,
		name: str,
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		_ = cwd
		validate_env_var_name(name)
		if env is not None and name in env:
			return env[name]
		return os.environ.get(name)

	def snapshot(self, session: RunSession) -> str | None:
		_ = session
		return None

	def stop(self, session: RunSession) -> None:
		_ = session

	def cleanup(self, session: RunSession) -> None:
		_ = session

	def runtime_result(self, session: RunSession) -> RuntimeInfo:
		_ = session
		return RuntimeInfo(
			mode=RuntimeMode.LOCAL,
			image=None,
			workspace_mount=str(session.host_workspace),
			container_id=None,
			saved_image=None,
			container_stopped=True,
		)


def get_runtime(mode: str | RuntimeMode, **kwargs: Any) -> BenchRuntime:
	runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(mode)
	if runtime_mode == RuntimeMode.LOCAL:
		return LocalRuntime(workspace=kwargs.get("workspace"))
	if runtime_mode == RuntimeMode.DOCKER:
		return DockerRuntime(image=kwargs.get("image"), gpu=bool(kwargs.get("gpu", False)))
	raise ValueError(f"unsupported runtime mode: {runtime_mode!r}")


def _run_to_file(
	cmd: list[str],
	*,
	output_path: Path,
	cwd: str | None = None,
	env: Mapping[str, str] | None = None,
	stdin_text: str | None = None,
	timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open("w", encoding="utf-8") as output:
		return subprocess.run(
			cmd,
			input=stdin_text,
			stdout=output,
			stderr=subprocess.STDOUT,
			text=True,
			cwd=cwd,
			env=env,
			timeout=timeout,
			check=False,
		)
