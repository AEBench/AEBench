"""Runtime execution modes for oracle checks.

This module defines the runtime abstraction used by oracle checks: local,
active-session, and Docker modes. It also selects an executor from the
configured oracle target and invocation context.
"""

from __future__ import annotations

import abc
import dataclasses
import os
import pathlib
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from models import (
	DockerImageOracleTargetConfig,
	LocalOracleTargetConfig,
	OracleInput,
	OracleTargetConfig,
	RuntimeMode,
	TaskOracleTargetConfig,
)
from runtime.backend import BenchRuntime, validate_env_var_name

from .process import (
	DEFAULT_MAX_CAPTURE_CHARS,
	ProcResult,
	proc_result_from_completed_process,
	proc_result_from_timeout,
	run_subprocess_capture,
)

PathLike = str | os.PathLike[str] | pathlib.Path


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimePath:
	"""A path expressed in the selected runtime's filesystem namespace."""

	value: pathlib.PurePosixPath

	@classmethod
	def from_parts(
		cls,
		*parts: str | pathlib.PurePosixPath,
	) -> "RuntimePath":
		"""Builds a runtime path from POSIX path components."""
		return cls(pathlib.PurePosixPath(*parts))

	def __str__(self) -> str:
		"""Returns the POSIX representation of the runtime path."""
		return self.value.as_posix()


CheckPath = PathLike | RuntimePath


_PATH_MOUNT_ORDER = (
	("workspace_dir", "/workspace"),
	("case_dir", "/case"),
	("refs_dir", "/refs"),
	("artifact_dir", "/artifact"),
	("output_dir", "/output"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class _PathMount:
	"""Maps a host directory into the runtime filesystem."""

	host_root: pathlib.Path
	runtime_root: pathlib.PurePosixPath

	def translate(
		self,
		path: pathlib.Path,
	) -> pathlib.PurePosixPath | None:
		"""Translates a host path contained by this mount.

		Args:
			path: Resolved host path to translate.

		Returns:
			The corresponding runtime path, or None when the path is outside
			this mount.
		"""
		try:
			relative = path.relative_to(self.host_root)
		except ValueError:
			return None

		if not relative.parts:
			return self.runtime_root

		return self.runtime_root.joinpath(*relative.parts)


def _resolved_path(path: PathLike) -> pathlib.Path:
	"""Returns an absolute normalized path without requiring it to exist."""
	return pathlib.Path(path).expanduser().resolve(strict=False)


class RuntimeCheckExecutor(abc.ABC):
	"""Executes oracle checks in a configured runtime."""

	def __init__(self, *, default_cwd: pathlib.Path) -> None:
		"""Initializes the executor with its default host working directory."""
		self._default_cwd = _resolved_path(default_cwd)

	def _resolve_host_path(self, path: PathLike) -> pathlib.Path:
		"""Resolves a host path relative to the executor workspace."""
		candidate = pathlib.Path(path).expanduser()
		if candidate.is_absolute():
			return candidate.resolve(strict=False)

		return (self._default_cwd / candidate).resolve(strict=False)

	@abc.abstractmethod
	def resolve_path(
		self,
		path: CheckPath,
	) -> pathlib.Path | pathlib.PurePosixPath:
		"""Resolves a path in the executor's filesystem namespace."""

	@property
	@abc.abstractmethod
	def path_separator(self) -> str:
		"""Returns the runtime path-list separator."""

	@abc.abstractmethod
	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Resolves an executable in the runtime."""

	@abc.abstractmethod
	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Reads an environment variable from the runtime."""

	@abc.abstractmethod
	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: CheckPath | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
		"""Runs a process and captures its output."""

	@abc.abstractmethod
	def path_exists(self, path: CheckPath) -> bool:
		"""Returns whether a path exists."""

	@abc.abstractmethod
	def path_is_file(self, path: CheckPath) -> bool:
		"""Returns whether a path is a regular file."""

	@abc.abstractmethod
	def path_is_dir(self, path: CheckPath) -> bool:
		"""Returns whether a path is a directory."""

	@abc.abstractmethod
	def read_file_text(
		self,
		path: CheckPath,
		encoding: str = "utf-8",
	) -> str:
		"""Reads a text file."""

	def close(self) -> None:
		"""Releases resources owned by the executor."""


def _translate_host_path(
	path: pathlib.Path,
	*,
	path_mounts: Sequence[_PathMount],
) -> pathlib.PurePosixPath:
	"""Translates a mounted host path into the runtime filesystem.

	Args:
		path: Host path to translate.
		path_mounts: Candidate host-to-runtime mappings.

	Returns:
		The corresponding path in the runtime filesystem.

	Raises:
		ValueError: If the host path is outside all configured mounts.
	"""
	resolved = _resolved_path(path)

	for mount in path_mounts:
		translated = mount.translate(resolved)
		if translated is not None:
			return translated

	raise ValueError(f"host path is not mounted in the oracle runtime: {resolved}")


def _prepare_runtime_command(
	cmd: str | Sequence[str],
	*,
	use_shell: bool,
) -> list[str]:
	"""Normalizes a command for execution in a POSIX runtime.

	Args:
		cmd: Shell source or an argument vector.
		use_shell: Whether to execute the command through ``sh -lc``.

	Returns:
		An argument vector suitable for the runtime backend.

	Raises:
		TypeError: If a string command is supplied without shell execution.
	"""
	if use_shell:
		shell_cmd = cmd if isinstance(cmd, str) else " ".join(shlex.quote(part) for part in cmd)
		return ["sh", "-lc", shell_cmd]

	if isinstance(cmd, str):
		raise TypeError("use_shell=False requires cmd to be a sequence of argv strings")

	return list(cmd)


def build_path_mounts(context: OracleInput) -> list[_PathMount]:
	"""Build host-to-runtime path mappings for an oracle invocation."""

	raw_mounts: list[tuple[pathlib.Path, pathlib.PurePosixPath]] = []
	refs_dir = context.case_dir / "refs"

	values: dict[str, pathlib.Path] = {
		"workspace_dir": context.workspace_dir,
		"case_dir": context.case_dir,
		"refs_dir": refs_dir,
		"artifact_dir": context.artifact_dir,
		"output_dir": context.output_dir,
	}

	for field_name, runtime_root_str in _PATH_MOUNT_ORDER:
		host_root = _resolved_path(values[field_name])

		if host_root.exists() or field_name in {"artifact_dir", "output_dir"}:
			raw_mounts.append(
				(
					host_root,
					pathlib.PurePosixPath(runtime_root_str),
				)
			)

	unique: dict[tuple[str, str], _PathMount] = {}

	for host_root, runtime_root_path in raw_mounts:
		unique[(str(host_root), str(runtime_root_path))] = _PathMount(
			host_root=host_root,
			runtime_root=runtime_root_path,
		)

	mounts = list(unique.values())
	mounts.sort(
		key=lambda mount: len(mount.host_root.parts),
		reverse=True,
	)
	return mounts


class LocalRuntimeCheckExecutor(RuntimeCheckExecutor):
	"""Executes oracle checks directly on the local host."""

	path_separator = os.pathsep

	def resolve_path(self, path: CheckPath) -> pathlib.Path:
		"""Resolves a host-backed or runtime-native local path."""
		if isinstance(path, RuntimePath):
			runtime_path = pathlib.Path(str(path.value))
			if runtime_path.is_absolute():
				return runtime_path.resolve(strict=False)

			return (self._default_cwd / runtime_path).resolve(strict=False)

		return self._resolve_host_path(path)

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Resolves an executable on the local host."""
		path_value = None if env is None else env.get("PATH")
		return shutil.which(executable, path=path_value)

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Reads an environment variable from the local host."""
		if env is not None and name in env:
			return env[name]

		return os.environ.get(name)

	def path_exists(self, path: CheckPath) -> bool:
		"""Returns whether a local path exists."""
		return self.resolve_path(path).exists()

	def path_is_file(self, path: CheckPath) -> bool:
		"""Returns whether a local path is a regular file."""
		return self.resolve_path(path).is_file()

	def path_is_dir(self, path: CheckPath) -> bool:
		"""Returns whether a local path is a directory."""
		return self.resolve_path(path).is_dir()

	def read_file_text(
		self,
		path: CheckPath,
		encoding: str = "utf-8",
	) -> str:
		"""Reads a text file from the local filesystem."""
		return self.resolve_path(path).read_text(encoding=encoding)

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: CheckPath | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
		"""Runs a process locally with environment overrides.

		The supplied environment augments the current process environment
		rather than replacing it.
		"""
		run_env = os.environ.copy()
		if env is not None:
			run_env.update(env)

		resolved_cwd = self._default_cwd if cwd is None else self.resolve_path(cwd)

		return run_subprocess_capture(
			cmd=cmd,
			cwd=resolved_cwd,
			env=run_env,
			timeout_seconds=timeout_seconds,
			use_shell=use_shell,
			capture_limit_chars=capture_limit_chars,
			drain_after_kill=drain_after_kill,
			encoding=encoding,
			on_chunk=on_chunk,
		)


class SessionRuntimeCheckExecutor(RuntimeCheckExecutor):
	"""Executes oracle checks through an active benchmark runtime."""

	def __init__(
		self,
		*,
		session: Any,
		runtime_backend: BenchRuntime,
		path_mounts: Sequence[_PathMount],
		default_cwd: pathlib.Path,
	) -> None:
		"""Initializes an executor backed by an active runtime session.

		Args:
			session: Active runtime session associated with the benchmark.
			runtime_backend: Backend used to execute runtime operations.
			path_mounts: Host-to-runtime path mappings.
			default_cwd: Default host working directory for checks.
		"""
		super().__init__(default_cwd=default_cwd)
		self._session = session
		self._runtime_backend = runtime_backend
		self._path_mounts = tuple(path_mounts)
		self._runtime_cwd = _translate_host_path(
			self._default_cwd,
			path_mounts=self._path_mounts,
		)

	def resolve_path(
		self,
		path: CheckPath,
	) -> pathlib.PurePosixPath:
		"""Resolves a host-backed or runtime-native session path."""
		if isinstance(path, RuntimePath):
			if path.value.is_absolute():
				return path.value

			return self._runtime_cwd.joinpath(path.value)

		return _translate_host_path(
			self._resolve_host_path(path),
			path_mounts=self._path_mounts,
		)

	@property
	def path_separator(self) -> str:
		"""Returns the path-list separator used by the active runtime."""
		return self._runtime_backend.path_separator

	def _translate_cwd(
		self,
		cwd: CheckPath | None,
	) -> str:
		"""Returns a runtime working directory accepted by the backend."""
		if cwd is None:
			return str(self._runtime_cwd)

		return str(self.resolve_path(cwd))

	def _path_matches(
		self,
		predicate: str,
		path: CheckPath,
	) -> bool:
		"""Evaluates a POSIX filesystem predicate in the active runtime.

		Runtime startup and communication failures are represented as a
		non-match. Invalid or unmounted host paths remain configuration errors.
		"""
		target = str(self.resolve_path(path))

		try:
			result = self._runtime_backend.run_process(
				["test", predicate, target],
				cwd=str(self._runtime_cwd),
				env=None,
				timeout=5.0,
			)
		except (
			OSError,
			RuntimeError,
			subprocess.TimeoutExpired,
		):
			return False

		return result.returncode == 0

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Resolves an executable through the active runtime backend."""
		return self._runtime_backend.resolve_executable(
			executable,
			cwd=str(self._runtime_cwd),
			env=None if env is None else dict(env),
		)

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Reads an environment variable through the active runtime backend."""
		return self._runtime_backend.read_env_var(
			name,
			cwd=str(self._runtime_cwd),
			env=None if env is None else dict(env),
		)

	def path_exists(self, path: CheckPath) -> bool:
		"""Returns whether a path exists in the active runtime."""
		return self._path_matches("-e", path)

	def path_is_file(self, path: CheckPath) -> bool:
		"""Returns whether a path is a regular file in the active runtime."""
		return self._path_matches("-f", path)

	def path_is_dir(self, path: CheckPath) -> bool:
		"""Returns whether a path is a directory in the active runtime."""
		return self._path_matches("-d", path)

	def read_file_text(
		self,
		path: CheckPath,
		encoding: str = "utf-8",
	) -> str:
		"""Reads a text file through the active runtime backend.

		The backend returns decoded text, so the encoding argument is accepted
		for interface compatibility but is not applied by this implementation.

		Raises:
			OSError: If the runtime command cannot read the requested file.
		"""
		_ = encoding
		target = str(self.resolve_path(path))
		result = self._runtime_backend.run_process(
			["cat", target],
			cwd=str(self._runtime_cwd),
			env=None,
			timeout=10.0,
		)
		if result.returncode != 0:
			detail = (result.stderr or "").strip()
			raise OSError(f"failed to read {path}: {detail}")

		return result.stdout or ""

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: CheckPath | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
		"""Runs a command through the active runtime.

		The active backend owns process execution. This adapter normalizes
		shell commands, resolves the working directory, and converts backend
		results and timeouts into ``ProcResult`` instances.
		"""
		_ = drain_after_kill
		_ = encoding

		run_cmd = _prepare_runtime_command(
			cmd,
			use_shell=use_shell,
		)

		try:
			result = self._runtime_backend.run_process(
				run_cmd,
				cwd=self._translate_cwd(cwd),
				env=None if env is None else dict(env),
				timeout=timeout_seconds,
			)
		except subprocess.TimeoutExpired as exc:
			return proc_result_from_timeout(
				exc,
				capture_limit_chars=capture_limit_chars,
				on_chunk=on_chunk,
			)

		return proc_result_from_completed_process(
			result,
			capture_limit_chars=capture_limit_chars,
			on_chunk=on_chunk,
		)


class DockerRuntimeCheckExecutor(RuntimeCheckExecutor):
	"""Executes oracle checks in a lazily created Docker container."""

	path_separator = ":"

	def __init__(
		self,
		*,
		image: str,
		path_mounts: Sequence[_PathMount],
		default_cwd: pathlib.Path,
		runtime_cwd: pathlib.PurePosixPath | None = None,
	) -> None:
		"""Initializes a Docker-backed executor.

		The container is not started until the first operation requires it.

		Args:
			image: Docker image used for oracle checks.
			path_mounts: Host directories exposed inside the container.
			default_cwd: Default host working directory for checks.
			runtime_cwd: Default working directory inside the container.

		Raises:
			ValueError: If runtime_cwd is relative.
		"""
		super().__init__(default_cwd=default_cwd)
		self._image = image
		self._path_mounts = tuple(path_mounts)

		if runtime_cwd is not None and not runtime_cwd.is_absolute():
			raise ValueError("runtime_cwd must be an absolute POSIX path")

		self._runtime_cwd = (
			runtime_cwd
			if runtime_cwd is not None
			else _translate_host_path(
				self._default_cwd,
				path_mounts=self._path_mounts,
			)
		)
		self._container_name = f"aebench-oracle-{int(time.time() * 1000)}"
		self._container_id: str | None = None

	def resolve_path(
		self,
		path: CheckPath,
	) -> pathlib.PurePosixPath:
		"""Resolves a host-backed or runtime-native container path."""
		if isinstance(path, RuntimePath):
			if path.value.is_absolute():
				return path.value

			return self._runtime_cwd.joinpath(path.value)

		return _translate_host_path(
			self._resolve_host_path(path),
			path_mounts=self._path_mounts,
		)

	def _ensure_container(self) -> str:
		"""Starts the check container if necessary and returns its ID.

		Returns:
			The Docker container ID.

		Raises:
			RuntimeError: If Docker cannot create the container.
		"""
		if self._container_id is not None:
			return self._container_id

		docker_cmd = [
			"docker",
			"run",
			"-d",
			"--init",
			"--name",
			self._container_name,
		]

		for mount in self._path_mounts:
			if mount.host_root.exists():
				docker_cmd.extend(
					[
						"-v",
						f"{mount.host_root}:{mount.runtime_root}",
					]
				)

		docker_cmd.extend(
			[
				"-w",
				str(self._runtime_cwd),
				self._image,
				"sleep",
				"infinity",
			]
		)

		result = subprocess.run(
			docker_cmd,
			capture_output=True,
			text=True,
			check=False,
		)
		if result.returncode != 0:
			detail = (result.stderr or result.stdout).strip() or "docker run failed"
			raise RuntimeError(detail)

		self._container_id = result.stdout.strip()
		return self._container_id

	def _docker_exec(
		self,
		*,
		cmd: list[str],
		cwd: CheckPath | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
	) -> subprocess.CompletedProcess[str]:
		"""Executes a command in the check container.

		Args:
			cmd: Argument vector to execute.
			cwd: Working directory for the command.
			env: Environment variables supplied to ``docker exec``.
			timeout_seconds: Maximum execution time.

		Returns:
			The completed ``docker exec`` process.

		Raises:
			subprocess.TimeoutExpired: If execution exceeds the timeout.
			RuntimeError: If the container cannot be started.
			ValueError: If a host working directory is not mounted.
		"""
		container_id = self._ensure_container()
		runtime_cwd = self._runtime_cwd if cwd is None else self.resolve_path(cwd)

		docker_cmd = [
			"docker",
			"exec",
			"-w",
			str(runtime_cwd),
		]

		if env:
			for key, value in env.items():
				docker_cmd.extend(["-e", f"{key}={value}"])

		docker_cmd.append(container_id)
		docker_cmd.extend(cmd)

		return subprocess.run(
			docker_cmd,
			capture_output=True,
			text=True,
			timeout=timeout_seconds,
			check=False,
		)

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Resolves an executable inside the check container."""
		command = f"command -v {shlex.quote(executable)}"

		try:
			result = self._docker_exec(
				cmd=["sh", "-lc", command],
				cwd=None,
				env=env,
				timeout_seconds=5.0,
			)
		except (
			OSError,
			RuntimeError,
			subprocess.TimeoutExpired,
		):
			return None

		if result.returncode != 0:
			return None

		resolved = result.stdout.strip()
		return resolved or None

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		"""Reads an environment variable inside the check container."""
		valid_name = validate_env_var_name(name)

		try:
			result = self._docker_exec(
				cmd=["printenv", valid_name],
				cwd=None,
				env=env,
				timeout_seconds=5.0,
			)
		except (
			OSError,
			RuntimeError,
			subprocess.TimeoutExpired,
		):
			return None

		if result.returncode != 0:
			return None

		return result.stdout.removesuffix("\n")

	def _path_matches(
		self,
		predicate: str,
		path: CheckPath,
	) -> bool:
		"""Evaluates a POSIX filesystem predicate in the container.

		Docker startup and communication failures are represented as a
		non-match. Invalid or unmounted host paths remain configuration errors.
		"""
		target = str(self.resolve_path(path))

		try:
			result = self._docker_exec(
				cmd=["test", predicate, target],
				cwd=None,
				env=None,
				timeout_seconds=5.0,
			)
		except (
			OSError,
			RuntimeError,
			subprocess.TimeoutExpired,
		):
			return False

		return result.returncode == 0

	def path_exists(self, path: CheckPath) -> bool:
		"""Returns whether a path exists in the check container."""
		return self._path_matches("-e", path)

	def path_is_file(self, path: CheckPath) -> bool:
		"""Returns whether a path is a regular file in the check container."""
		return self._path_matches("-f", path)

	def path_is_dir(self, path: CheckPath) -> bool:
		"""Returns whether a path is a directory in the check container."""
		return self._path_matches("-d", path)

	def read_file_text(
		self,
		path: CheckPath,
		encoding: str = "utf-8",
	) -> str:
		"""Reads a text file from the check container.

		Docker returns decoded text because ``subprocess.run`` uses text mode.
		The encoding argument is accepted for interface compatibility but is
		not applied by this implementation.

		Raises:
			OSError: If Docker cannot execute the command or read the file.
			ValueError: If a host path is not mounted.
		"""
		_ = encoding
		target = str(self.resolve_path(path))

		try:
			result = self._docker_exec(
				cmd=["cat", target],
				cwd=None,
				env=None,
				timeout_seconds=10.0,
			)
		except (
			OSError,
			RuntimeError,
			subprocess.TimeoutExpired,
		) as exc:
			raise OSError(f"failed to read {path}: {exc}") from exc

		if result.returncode != 0:
			detail = (result.stderr or "").strip()
			raise OSError(f"failed to read {path}: {detail}")

		return result.stdout or ""

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: CheckPath | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
		"""Runs a command in the check container and captures its output.

		This adapter normalizes shell commands and converts Docker process
		results and timeouts into ``ProcResult`` instances.
		"""
		_ = drain_after_kill
		_ = encoding

		run_cmd = _prepare_runtime_command(
			cmd,
			use_shell=use_shell,
		)

		try:
			result = self._docker_exec(
				cmd=run_cmd,
				cwd=cwd,
				env=env,
				timeout_seconds=timeout_seconds,
			)
		except subprocess.TimeoutExpired as exc:
			return proc_result_from_timeout(
				exc,
				capture_limit_chars=capture_limit_chars,
				on_chunk=on_chunk,
			)

		return proc_result_from_completed_process(
			result,
			capture_limit_chars=capture_limit_chars,
			on_chunk=on_chunk,
		)


def _build_task_runtime_check_executor(
	context: OracleInput,
) -> RuntimeCheckExecutor:
	"""Builds an executor for the task runtime."""
	if context.runtime_session is not None and context.runtime_backend is not None:
		return SessionRuntimeCheckExecutor(
			session=context.runtime_session,
			runtime_backend=cast(
				BenchRuntime,
				context.runtime_backend,
			),
			path_mounts=build_path_mounts(context),
			default_cwd=context.workspace_dir,
		)

	runtime_result = context.runtime_result
	if runtime_result is None or runtime_result.runtime.mode == RuntimeMode.LOCAL:
		return LocalRuntimeCheckExecutor(
			default_cwd=context.workspace_dir,
		)

	if runtime_result.runtime.mode != RuntimeMode.DOCKER:
		raise RuntimeError(
			"Cannot build oracle runtime executor: "
			"unsupported task runtime mode "
			f"{runtime_result.runtime.mode!r}."
		)

	image = runtime_result.runtime.saved_image or runtime_result.runtime.image
	if not image:
		raise RuntimeError(
			"Cannot build oracle runtime executor: task Docker runtime has no image."
		)

	return DockerRuntimeCheckExecutor(
		image=image,
		path_mounts=build_path_mounts(context),
		default_cwd=context.workspace_dir,
	)


class OracleRuntimeRegistry:
	"""Builds and caches executors for named oracle targets."""

	def __init__(self, *, context: OracleInput) -> None:
		"""Initializes a registry for one oracle invocation.

		Args:
			context: Oracle invocation context containing target definitions.
		"""
		self._context = context
		self._targets = dict(context.oracle_targets)
		self._path_mounts = tuple(build_path_mounts(context))
		self._executors: dict[
			str,
			RuntimeCheckExecutor,
		] = {}

	def executor_for(
		self,
		target_name: str,
	) -> RuntimeCheckExecutor:
		"""Returns the cached executor for a named target.

		Args:
			target_name: Name from ``oracle.targets`` or a built-in target.

		Returns:
			The executor associated with the target.

		Raises:
			KeyError: If the target name is not configured.
			RuntimeError: If the target cannot be constructed.
		"""
		try:
			return self._executors[target_name]
		except KeyError:
			pass

		try:
			target = self._targets[target_name]
		except KeyError as exc:
			available = ", ".join(sorted(self._targets))
			raise KeyError(
				f"unknown oracle target {target_name!r}; available targets: {available}"
			) from exc

		executor = self._build_executor(target)
		self._executors[target_name] = executor
		return executor

	def _build_executor(
		self,
		target: OracleTargetConfig,
	) -> RuntimeCheckExecutor:
		"""Builds an executor for a target definition."""
		if isinstance(
			target,
			LocalOracleTargetConfig,
		):
			return LocalRuntimeCheckExecutor(
				default_cwd=self._context.workspace_dir,
			)

		if isinstance(
			target,
			TaskOracleTargetConfig,
		):
			return _build_task_runtime_check_executor(self._context)

		if isinstance(
			target,
			DockerImageOracleTargetConfig,
		):
			return DockerRuntimeCheckExecutor(
				image=target.image,
				path_mounts=self._path_mounts,
				default_cwd=(self._context.workspace_dir),
				runtime_cwd=pathlib.PurePosixPath(target.working_dir),
			)

		raise RuntimeError(f"unsupported oracle target type: {type(target).__name__}")

	def close(self) -> None:
		"""Closes all executors constructed by this registry."""
		errors: list[tuple[str, Exception]] = []

		for target_name, executor in reversed(list(self._executors.items())):
			try:
				executor.close()
			except Exception as exc:
				errors.append((target_name, exc))

		self._executors.clear()

		if errors:
			target_name, error = errors[0]
			raise RuntimeError(f"failed to close oracle target {target_name!r}: {error}") from error


def build_oracle_runtime_registry(
	context: OracleInput,
) -> OracleRuntimeRegistry:
	"""Builds the runtime registry for an oracle invocation."""
	return OracleRuntimeRegistry(context=context)


def resolve_check_executable(
	executable: str,
	*,
	executor: RuntimeCheckExecutor | None,
	env: Mapping[str, str] | None = None,
) -> str | None:
	"""Resolves an executable through an executor or on the local host."""
	if executor is not None:
		return executor.resolve_executable(executable, env=env)

	path_value = None if env is None else env.get("PATH")
	return shutil.which(executable, path=path_value)


def read_check_env_var(
	name: str,
	*,
	executor: RuntimeCheckExecutor | None,
	env: Mapping[str, str] | None = None,
) -> str | None:
	"""Reads an environment variable through an executor or locally."""
	if executor is not None:
		return executor.read_env_var(name, env=env)

	if env is not None and name in env:
		return env[name]

	return os.environ.get(name)


def get_check_path_separator(
	*,
	executor: RuntimeCheckExecutor | None,
) -> str:
	"""Returns the path-list separator used by the selected runtime."""
	if executor is not None:
		return executor.path_separator

	return os.pathsep


def path_from_user_input(value: PathLike) -> pathlib.Path:
	"""Converts a user-supplied host path to ``pathlib.Path``."""
	return pathlib.Path(os.fspath(value))


def _resolve_local_check_path(path: CheckPath) -> pathlib.Path:
	"""Resolves a check path without an explicit executor."""
	if isinstance(path, RuntimePath):
		candidate = pathlib.Path(str(path.value))
		if candidate.is_absolute():
			return candidate.resolve(strict=False)

		return (pathlib.Path.cwd() / candidate).resolve(strict=False)

	return pathlib.Path(path).expanduser().resolve(strict=False)


def run_check_process_capture(
	*,
	cmd: str | Sequence[str],
	cwd: CheckPath | None,
	env: Mapping[str, str] | None,
	timeout_seconds: float,
	use_shell: bool = False,
	capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
	drain_after_kill: bool = False,
	encoding: str | None = None,
	on_chunk: Callable[[str, str], None] | None = None,
	executor: RuntimeCheckExecutor | None = None,
) -> ProcResult:
	"""Runs a check process through an executor or on the local host.

	When no executor is supplied, environment overrides augment the current
	process environment.
	"""
	if executor is not None:
		return executor.run_process_capture(
			cmd=cmd,
			cwd=cwd,
			env=env,
			timeout_seconds=timeout_seconds,
			use_shell=use_shell,
			capture_limit_chars=capture_limit_chars,
			drain_after_kill=drain_after_kill,
			encoding=encoding,
			on_chunk=on_chunk,
		)

	run_env: Mapping[str, str] | None
	if env is None:
		run_env = None
	else:
		merged = os.environ.copy()
		merged.update(env)
		run_env = merged

	resolved_cwd = None if cwd is None else _resolve_local_check_path(cwd)

	return run_subprocess_capture(
		cmd=cmd,
		cwd=resolved_cwd,
		env=run_env,
		timeout_seconds=timeout_seconds,
		use_shell=use_shell,
		capture_limit_chars=capture_limit_chars,
		drain_after_kill=drain_after_kill,
		encoding=encoding,
		on_chunk=on_chunk,
	)


def check_path_exists(
	path: CheckPath,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> bool:
	"""Returns whether a path exists in the selected runtime."""
	if executor is not None:
		return executor.path_exists(path)

	return _resolve_local_check_path(path).exists()


def check_path_is_file(
	path: CheckPath,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> bool:
	"""Returns whether a path is a file in the selected runtime."""
	if executor is not None:
		return executor.path_is_file(path)

	return _resolve_local_check_path(path).is_file()


def check_path_is_dir(
	path: CheckPath,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> bool:
	"""Returns whether a path is a directory in the selected runtime."""
	if executor is not None:
		return executor.path_is_dir(path)

	return _resolve_local_check_path(path).is_dir()


def check_read_file_text(
	path: CheckPath,
	*,
	encoding: str = "utf-8",
	executor: RuntimeCheckExecutor | None = None,
) -> str:
	"""Reads a text file from the selected runtime."""
	if executor is not None:
		return executor.read_file_text(
			path,
			encoding=encoding,
		)

	return _resolve_local_check_path(path).read_text(encoding=encoding)
