"""Runtime oracle infrastructure."""

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
from typing import Any, Protocol, cast, runtime_checkable

from models import OracleInput, RuntimeMode
from runtime.backend import BenchRuntime, validate_env_var_name

from .process import (
	DEFAULT_MAX_CAPTURE_CHARS,
	ProcResult,
	proc_result_from_completed_process,
	proc_result_from_timeout,
	run_subprocess_capture,
)

PathLike = str | os.PathLike[str] | pathlib.Path


_PATH_MOUNT_ORDER = (
	("workspace_dir", "/workspace"),
	("case_dir", "/case"),
	("refs_dir", "/refs"),
	("artifact_dir", "/artifact"),
	("output_dir", "/output"),
)


@dataclasses.dataclass(frozen=True, slots=True)
class _PathMount:
	host_root: pathlib.Path
	runtime_root: pathlib.PurePosixPath

	def translate(self, path: pathlib.Path) -> pathlib.PurePosixPath | None:
		try:
			relative = path.relative_to(self.host_root)
		except ValueError:
			return None
		if not relative.parts:
			return self.runtime_root
		return self.runtime_root.joinpath(*relative.parts)


def _resolved_path(path: PathLike) -> pathlib.Path:
	return pathlib.Path(path).expanduser().resolve(strict=False)


class RuntimeCheckExecutor(abc.ABC):
	"""Executes oracle checks in a configured runtime."""

	def __init__(self, *, default_cwd: pathlib.Path) -> None:
		self._default_cwd = _resolved_path(default_cwd)

	def _effective_cwd(
		self,
		cwd: pathlib.Path | None,
	) -> pathlib.Path:
		return self._default_cwd if cwd is None else cwd

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
		cwd: pathlib.Path | None,
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
	def path_exists(self, path: pathlib.Path) -> bool:
		"""Returns whether a path exists."""

	@abc.abstractmethod
	def path_is_file(self, path: pathlib.Path) -> bool:
		"""Returns whether a path is a regular file."""

	@abc.abstractmethod
	def path_is_dir(self, path: pathlib.Path) -> bool:
		"""Returns whether a path is a directory."""

	@abc.abstractmethod
	def read_file_text(
		self,
		path: pathlib.Path,
		encoding: str = "utf-8",
	) -> str:
		"""Reads a text file."""

	def close(self) -> None:
		"""Releases resources owned by the executor."""


def _translate_runtime_path(
	path: pathlib.Path,
	*,
	path_mounts: Sequence[_PathMount],
) -> pathlib.PurePosixPath:
	resolved = _resolved_path(path)

	for mount in path_mounts:
		translated = mount.translate(resolved)
		if translated is not None:
			return translated

	return pathlib.PurePosixPath(
		str(resolved).replace(os.sep, "/")
	)


def _prepare_runtime_command(
	cmd: str | Sequence[str],
	*,
	use_shell: bool,
) -> list[str]:
	if use_shell:
		shell_cmd = (
			cmd
			if isinstance(cmd, str)
			else " ".join(shlex.quote(part) for part in cmd)
		)
		return ["sh", "-lc", shell_cmd]

	if isinstance(cmd, str):
		raise TypeError(
			"use_shell=False requires cmd to be a sequence of argv strings"
		)

	return list(cmd)


def build_path_mounts(context: OracleInput) -> list[_PathMount]:
	raw_mounts: list[tuple[pathlib.Path, pathlib.PurePosixPath]] = []
	refs_dir = context.case_dir / "refs"
	values: dict[str, pathlib.Path] = {
		"workspace_dir": context.workspace_dir,
		"case_dir": context.case_dir,
		"refs_dir": refs_dir,
		"artifact_dir": context.artifact_dir,
		"output_dir": context.output_dir,
	}
	for field_name, runtime_root in _PATH_MOUNT_ORDER:
		host_root = _resolved_path(values[field_name])
		if host_root.exists() or field_name in {"artifact_dir", "output_dir"}:
			raw_mounts.append((host_root, pathlib.PurePosixPath(runtime_root)))

	unique: dict[tuple[str, str], _PathMount] = {}
	for host_root, runtime_root_path in raw_mounts:
		unique[(str(host_root), str(runtime_root_path))] = _PathMount(host_root, runtime_root_path)

	mounts = list(unique.values())
	mounts.sort(key=lambda mount: len(mount.host_root.parts), reverse=True)
	return mounts


class LocalRuntimeCheckExecutor(RuntimeCheckExecutor):
	path_separator = ":"

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		path_value = None if env is None else env.get("PATH")
		return shutil.which(executable, path=path_value)

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		if env is not None and name in env:
			return env[name]
		return os.environ.get(name)

	def path_exists(self, path: pathlib.Path) -> bool:
		return path.exists()

	def path_is_file(self, path: pathlib.Path) -> bool:
		return path.is_file()

	def path_is_dir(self, path: pathlib.Path) -> bool:
		return path.is_dir()

	def read_file_text(self, path: pathlib.Path, encoding: str = "utf-8") -> str:
		return path.read_text(encoding=encoding)

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: pathlib.Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
		run_env = os.environ.copy()
		if env is not None:
			run_env.update(env)
		return run_subprocess_capture(
			cmd=cmd,
			cwd=self._effective_cwd(cwd),
			env=run_env,
			timeout_seconds=timeout_seconds,
			use_shell=use_shell,
			capture_limit_chars=capture_limit_chars,
			drain_after_kill=drain_after_kill,
			encoding=encoding,
			on_chunk=on_chunk,
		)


class SessionRuntimeCheckExecutor(RuntimeCheckExecutor):
	def __init__(
		self,
		*,
		session: Any,
		runtime_backend: BenchRuntime,
		path_mounts: Sequence[_PathMount],
		default_cwd: pathlib.Path,
	) -> None:
		super().__init__(default_cwd=default_cwd)
		self._session = session
		self._runtime_backend = runtime_backend
		self._path_mounts = tuple(path_mounts)

	def _translate_path(
		self,
		path: pathlib.Path | None,
	) -> pathlib.PurePosixPath:
		return _translate_runtime_path(
			self._effective_cwd(path),
			path_mounts=self._path_mounts,
		)

	@property
	def path_separator(self) -> str:
		return self._runtime_backend.path_separator

	def _translate_cwd(
		self,
		cwd: pathlib.Path | None,
	) -> str:
		return str(self._translate_path(cwd))

	def _run_test(self, flag: str, path: pathlib.Path) -> bool:
		try:
			result = self._runtime_backend.run_process(
				["test", flag, self._translate_cwd(path) or str(path)],
				cwd=self._translate_cwd(None),
				env=None,
				timeout=5.0,
			)
		except (OSError, RuntimeError, subprocess.TimeoutExpired):
			return False
		return result.returncode == 0

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		return self._runtime_backend.resolve_executable(
			executable,
			cwd=self._translate_cwd(None),
			env=None if env is None else dict(env),
		)

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		return self._runtime_backend.read_env_var(
			name,
			cwd=self._translate_cwd(None),
			env=None if env is None else dict(env),
		)

	def path_exists(self, path: pathlib.Path) -> bool:
		return self._run_test("-e", path)

	def path_is_file(self, path: pathlib.Path) -> bool:
		return self._run_test("-f", path)

	def path_is_dir(self, path: pathlib.Path) -> bool:
		return self._run_test("-d", path)

	def read_file_text(self, path: pathlib.Path, encoding: str = "utf-8") -> str:
		_ = encoding
		target = self._translate_cwd(path)
		result = self._runtime_backend.run_process(
			["cat", target],
			cwd=self._translate_cwd(None),
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
		cwd: pathlib.Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
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
	path_separator = ":"

	def __init__(
		self,
		*,
		image: str,
		path_mounts: Sequence[_PathMount],
		default_cwd: pathlib.Path,
	) -> None:
		super().__init__(default_cwd=default_cwd)
		self._image = image
		self._path_mounts = tuple(path_mounts)
		self._container_name = (
			f"aebench-oracle-{int(time.time() * 1000)}"
		)
		self._container_id: str | None = None

	def _translate_path(
		self,
		path: pathlib.Path | None,
	) -> pathlib.PurePosixPath:
		return _translate_runtime_path(
			self._effective_cwd(path),
			path_mounts=self._path_mounts,
		)

	def _ensure_container(self) -> str:
		if self._container_id is not None:
			return self._container_id

		docker_cmd = ["docker", "run", "-d", "--init", "--name", self._container_name]
		for mount in self._path_mounts:
			if mount.host_root.exists():
				docker_cmd.extend(["-v", f"{mount.host_root}:{mount.runtime_root}"])
		docker_cmd.extend(
			["-w", str(self._translate_path(None))]
		)
		docker_cmd.extend([self._image, "sleep", "infinity"])

		result = subprocess.run(docker_cmd, capture_output=True, text=True, check=False)
		if result.returncode != 0:
			detail = (result.stderr or result.stdout).strip() or "docker run failed"
			raise RuntimeError(detail)
		self._container_id = result.stdout.strip()
		return self._container_id

	def _docker_exec(
		self,
		*,
		cmd: list[str],
		cwd: pathlib.Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
	) -> subprocess.CompletedProcess[str]:
		container_id = self._ensure_container()
		docker_cmd = ["docker", "exec"]
		docker_cmd.extend(
			["-w", str(self._translate_path(cwd))]
		)
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
		command = f"command -v {shlex.quote(executable)}"
		try:
			result = self._docker_exec(
				cmd=["sh", "-lc", command],
				cwd=None,
				env=env,
				timeout_seconds=5.0,
			)
		except (OSError, RuntimeError, subprocess.TimeoutExpired):
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
		valid_name = validate_env_var_name(name)
		try:
			result = self._docker_exec(
				cmd=["printenv", valid_name],
				cwd=None,
				env=env,
				timeout_seconds=5.0,
			)
		except (OSError, RuntimeError, subprocess.TimeoutExpired):
			return None
		if result.returncode != 0:
			return None
		return result.stdout.removesuffix("\n")

	def _run_test(self, flag: str, path: pathlib.Path) -> bool:
		target = str(self._translate_path(path))
		try:
			result = self._docker_exec(
				cmd=["test", flag, target],
				cwd=None,
				env=None,
				timeout_seconds=5.0,
			)
		except (OSError, RuntimeError, subprocess.TimeoutExpired):
			return False
		return result.returncode == 0

	def path_exists(self, path: pathlib.Path) -> bool:
		return self._run_test("-e", path)

	def path_is_file(self, path: pathlib.Path) -> bool:
		return self._run_test("-f", path)

	def path_is_dir(self, path: pathlib.Path) -> bool:
		return self._run_test("-d", path)

	def read_file_text(self, path: pathlib.Path, encoding: str = "utf-8") -> str:
		_ = encoding
		target = str(self._translate_path(path) or path)
		try:
			result = self._docker_exec(
				cmd=["cat", target],
				cwd=None,
				env=None,
				timeout_seconds=10.0,
			)
		except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
			raise OSError(f"failed to read {path}: {exc}") from exc
		if result.returncode != 0:
			detail = (result.stderr or "").strip()
			raise OSError(f"failed to read {path}: {detail}")
		return result.stdout or ""

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: pathlib.Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
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


def build_runtime_check_executor(
	context: OracleInput,
) -> RuntimeCheckExecutor:
	"""Builds the executor used by oracle checks."""
	oracle_runtime = getattr(
		context.oracle_config,
		"runtime",
		None,
	)
	oracle_mode = getattr(
		oracle_runtime,
		"mode",
		RuntimeMode.INHERIT,
	)

	# Explicit oracle configuration overrides runtime inheritance.
	if oracle_mode == RuntimeMode.LOCAL:
		return LocalRuntimeCheckExecutor(
			default_cwd=context.workspace_dir,
		)

	if oracle_mode == RuntimeMode.DOCKER:
		image = getattr(oracle_runtime, "image", None)
		if not image:
			raise RuntimeError(
				"Cannot build oracle runtime executor: "
				"Docker mode requires an image."
			)

		return DockerRuntimeCheckExecutor(
			image=image,
			path_mounts=build_path_mounts(context),
			default_cwd=context.workspace_dir,
		)

	if oracle_mode != RuntimeMode.INHERIT:
		raise ValueError(
			f"unsupported oracle runtime mode: "
			f"{oracle_mode!r}"
		)

	# Otherwise, inherit the task runtime.
	if (
		context.runtime_session is not None
		and context.runtime_backend is not None
	):
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
	if (
		runtime_result is None
		or runtime_result.runtime.mode == RuntimeMode.LOCAL
	):
		return LocalRuntimeCheckExecutor(
			default_cwd=context.workspace_dir,
		)

	if runtime_result.runtime.mode != RuntimeMode.DOCKER:
		raise RuntimeError(
			"Cannot build oracle runtime executor: "
			"unsupported inherited runtime mode "
			f"{runtime_result.runtime.mode!r}."
		)
	image = (
		runtime_result.runtime.saved_image
		or runtime_result.runtime.image
	)
	if not image:
		raise RuntimeError(
			"Cannot build oracle runtime executor: "
			"inherited Docker runtime has no image."
		)

	return DockerRuntimeCheckExecutor(
		image=image,
		path_mounts=build_path_mounts(context),
		default_cwd=context.workspace_dir,
	)


def resolve_check_executable(
	executable: str,
	*,
	executor: RuntimeCheckExecutor | None,
	env: Mapping[str, str] | None = None,
) -> str | None:
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
	if executor is not None:
		return executor.read_env_var(name, env=env)
	if env is not None and name in env:
		return env[name]
	return os.environ.get(name)


def get_check_path_separator(*, executor: RuntimeCheckExecutor | None) -> str:
	if executor is not None:
		return executor.path_separator
	return os.pathsep


def path_from_user_input(value: PathLike) -> pathlib.Path:
	return pathlib.Path(os.fspath(value))


def run_check_process_capture(
	*,
	cmd: str | Sequence[str],
	cwd: pathlib.Path | None,
	env: Mapping[str, str] | None,
	timeout_seconds: float,
	use_shell: bool = False,
	capture_limit_chars: int = DEFAULT_MAX_CAPTURE_CHARS,
	drain_after_kill: bool = False,
	encoding: str | None = None,
	on_chunk: Callable[[str, str], None] | None = None,
	executor: RuntimeCheckExecutor | None = None,
) -> ProcResult:
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

	return run_subprocess_capture(
		cmd=cmd,
		cwd=cwd,
		env=run_env,
		timeout_seconds=timeout_seconds,
		use_shell=use_shell,
		capture_limit_chars=capture_limit_chars,
		drain_after_kill=drain_after_kill,
		encoding=encoding,
		on_chunk=on_chunk,
	)


def check_path_exists(
	path: pathlib.Path,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> bool:
	if executor is not None:
		return executor.path_exists(path)
	return path.exists()


def check_path_is_file(
	path: pathlib.Path,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> bool:
	if executor is not None:
		return executor.path_is_file(path)
	return path.is_file()


def check_path_is_dir(
	path: pathlib.Path,
	*,
	executor: RuntimeCheckExecutor | None = None,
) -> bool:
	if executor is not None:
		return executor.path_is_dir(path)
	return path.is_dir()


def check_read_file_text(
	path: pathlib.Path,
	*,
	encoding: str = "utf-8",
	executor: RuntimeCheckExecutor | None = None,
) -> str:
	if executor is not None:
		return executor.read_file_text(path, encoding)
	return path.read_text(encoding=encoding)
