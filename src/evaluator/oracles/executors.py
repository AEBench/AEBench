"""Runtime oracle infrastructure."""

from __future__ import annotations

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
	decode_text,
	run_subprocess_capture,
	truncate_text,
)

PathLike = str | os.PathLike[str] | pathlib.Path


_PATH_MOUNT_ORDER = (
	("workspace_dir", "/workspace"),
	("case_dir", "/case"),
	("refs_dir", "/refs"),
	("artifact_dir", "/artifact"),
	("output_dir", "/output"),
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


@runtime_checkable
class RuntimeCheckExecutor(Protocol):
	@property
	def path_separator(self) -> str:
		raise NotImplementedError

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		raise NotImplementedError

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		raise NotImplementedError

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
		raise NotImplementedError

	def path_exists(self, path: pathlib.Path) -> bool:
		raise NotImplementedError

	def path_is_file(self, path: pathlib.Path) -> bool:
		raise NotImplementedError

	def path_is_dir(self, path: pathlib.Path) -> bool:
		raise NotImplementedError

	def read_file_text(self, path: pathlib.Path, encoding: str = "utf-8") -> str:
		raise NotImplementedError

	def close(self) -> None:
		raise NotImplementedError


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


class LocalRuntimeCheckExecutor:
	path_separator = os.pathsep

	def __init__(self, *, default_cwd: pathlib.Path) -> None:
		self._default_cwd = _resolved_path(default_cwd)

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
			cwd=cwd or self._default_cwd,
			env=run_env,
			timeout_seconds=timeout_seconds,
			use_shell=use_shell,
			capture_limit_chars=capture_limit_chars,
			drain_after_kill=drain_after_kill,
			encoding=encoding,
			on_chunk=on_chunk,
		)

	def close(self) -> None:
		return None


class _MappedRuntimeExecutor:
	def __init__(self, *, path_mounts: Sequence[_PathMount], default_cwd: pathlib.Path) -> None:
		self._path_mounts = tuple(path_mounts)
		self._default_cwd = _resolved_path(default_cwd)

	def _translate_path(self, path: pathlib.Path | None) -> pathlib.PurePosixPath | None:
		if path is None:
			return self._translate_path(self._default_cwd)
		resolved = _resolved_path(path)
		for mount in self._path_mounts:
			translated = mount.translate(resolved)
			if translated is not None:
				return translated
		return pathlib.PurePosixPath(str(resolved).replace(os.sep, "/"))


class SessionRuntimeCheckExecutor(_MappedRuntimeExecutor):
	def __init__(
		self,
		*,
		session: Any,
		runtime_backend: BenchRuntime,
		path_mounts: Sequence[_PathMount],
		default_cwd: pathlib.Path,
	) -> None:
		super().__init__(path_mounts=path_mounts, default_cwd=default_cwd)
		self._session = session
		self._runtime_backend = runtime_backend

	@property
	def path_separator(self) -> str:
		return self._runtime_backend.path_separator

	def _translate_cwd(self, cwd: pathlib.Path | None) -> str | None:
		translated = self._translate_path(cwd)
		if translated is not None:
			return str(translated)
		translate_host_path = getattr(self._session, "translate_host_path", None)
		if callable(translate_host_path):
			return cast(str | None, translate_host_path(cwd))
		return None if cwd is None else str(cwd)

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
		target = self._translate_cwd(path) or str(path)
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
		if use_shell:
			shell_cmd = cmd if isinstance(cmd, str) else " ".join(shlex.quote(part) for part in cmd)
			run_cmd = ["sh", "-lc", shell_cmd]
		else:
			if isinstance(cmd, str):
				raise TypeError("use_shell=False requires cmd to be a sequence of argv strings")
			run_cmd = list(cmd)

		try:
			result = self._runtime_backend.run_process(
				run_cmd,
				cwd=self._translate_cwd(cwd),
				env=None if env is None else dict(env),
				timeout=timeout_seconds,
			)
		except subprocess.TimeoutExpired as exc:
			stdout = decode_text(exc.stdout)
			stderr = decode_text(exc.stderr)
			if on_chunk is not None:
				if stdout:
					on_chunk("stdout", stdout)
				if stderr:
					on_chunk("stderr", stderr)
			return ProcResult(
				returncode=None,
				stdout=truncate_text(stdout, capture_limit_chars),
				stderr=truncate_text(stderr, capture_limit_chars),
				timed_out=True,
			)

		stdout = result.stdout or ""
		stderr = result.stderr or ""
		if on_chunk is not None:
			if stdout:
				on_chunk("stdout", stdout)
			if stderr:
				on_chunk("stderr", stderr)
		return ProcResult(
			returncode=result.returncode,
			stdout=truncate_text(stdout, capture_limit_chars),
			stderr=truncate_text(stderr, capture_limit_chars),
			timed_out=False,
		)

	def close(self) -> None:
		return None


class DockerRuntimeCheckExecutor(_MappedRuntimeExecutor):
	path_separator = ":"

	def __init__(
		self,
		*,
		image: str,
		path_mounts: Sequence[_PathMount],
		default_cwd: pathlib.Path,
	) -> None:
		super().__init__(path_mounts=path_mounts, default_cwd=default_cwd)
		self._image = image
		self._container_name = f"aebench-oracle-{int(time.time() * 1000)}"
		self._container_id: str | None = None

	def _ensure_container(self) -> str:
		if self._container_id is not None:
			return self._container_id

		docker_cmd = ["docker", "run", "-d", "--init", "--name", self._container_name]
		for mount in self._path_mounts:
			if mount.host_root.exists():
				docker_cmd.extend(["-v", f"{mount.host_root}:{mount.runtime_root}"])
		translated_cwd = self._translate_path(None)
		if translated_cwd is not None:
			docker_cmd.extend(["-w", str(translated_cwd)])
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
		translated_cwd = self._translate_path(cwd)
		if translated_cwd is not None:
			docker_cmd.extend(["-w", str(translated_cwd)])
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
		target = str(self._translate_path(path) or path)
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
		if use_shell:
			shell_cmd = cmd if isinstance(cmd, str) else " ".join(shlex.quote(part) for part in cmd)
			run_cmd = ["sh", "-lc", shell_cmd]
		else:
			if isinstance(cmd, str):
				raise TypeError("use_shell=False requires cmd to be a sequence of argv strings")
			run_cmd = list(cmd)

		try:
			result = self._docker_exec(
				cmd=run_cmd,
				cwd=cwd,
				env=env,
				timeout_seconds=timeout_seconds,
			)
		except subprocess.TimeoutExpired as exc:
			stdout = decode_text(exc.stdout)
			stderr = decode_text(exc.stderr)
			if on_chunk is not None:
				if stdout:
					on_chunk("stdout", stdout)
				if stderr:
					on_chunk("stderr", stderr)
			return ProcResult(
				returncode=None,
				stdout=truncate_text(stdout, capture_limit_chars),
				stderr=truncate_text(stderr, capture_limit_chars),
				timed_out=True,
			)

		stdout = result.stdout or ""
		stderr = result.stderr or ""
		if on_chunk is not None:
			if stdout:
				on_chunk("stdout", stdout)
			if stderr:
				on_chunk("stderr", stderr)
		return ProcResult(
			returncode=result.returncode,
			stdout=truncate_text(stdout, capture_limit_chars),
			stderr=truncate_text(stderr, capture_limit_chars),
			timed_out=False,
		)

	def close(self) -> None:
		target = self._container_id or self._container_name
		subprocess.run(["docker", "rm", "-f", target], capture_output=True, text=True, check=False)
		self._container_id = None


class UnavailableRuntimeCheckExecutor:
	def __init__(self, *, message: str) -> None:
		self._message = message

	@property
	def path_separator(self) -> str:
		raise RuntimeError(self._message)

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		raise RuntimeError(self._message)

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		raise RuntimeError(self._message)

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
		raise RuntimeError(self._message)

	def path_exists(self, path: pathlib.Path) -> bool:
		raise RuntimeError(self._message)

	def path_is_file(self, path: pathlib.Path) -> bool:
		raise RuntimeError(self._message)

	def path_is_dir(self, path: pathlib.Path) -> bool:
		raise RuntimeError(self._message)

	def read_file_text(self, path: pathlib.Path, encoding: str = "utf-8") -> str:
		raise RuntimeError(self._message)

	def close(self) -> None:
		return None


def build_runtime_check_executor(context: OracleInput) -> RuntimeCheckExecutor:
	"""Build the executor used by oracle checks."""
	path_mounts = build_path_mounts(context)
	runtime_result = context.runtime_result
	oracle_runtime = getattr(context.oracle_config, "runtime", None)
	oracle_mode = getattr(oracle_runtime, "mode", RuntimeMode.INHERIT)

	# Use the Docker runtime; run oracles inside Docker
	if oracle_mode == RuntimeMode.DOCKER:
		# NOTE: oracle_image is guaranteed not NULL/None by OracleRuntimeConfig validation
		oracle_image: str = getattr(oracle_runtime, "image", "")
		return cast(
			RuntimeCheckExecutor,
			DockerRuntimeCheckExecutor(
				image=oracle_image,
				path_mounts=path_mounts,
				default_cwd=context.workspace_dir,
			),
		)

	# Use the local runtime; run oracles on local machine
	if oracle_mode == RuntimeMode.LOCAL:
		return cast(
			RuntimeCheckExecutor,
			LocalRuntimeCheckExecutor(default_cwd=context.workspace_dir),
		)

	# Inherit the task runtime when requested; run oracles inside inherited runtime
	if oracle_mode != RuntimeMode.INHERIT:
		raise ValueError(f"unsupported oracle runtime mode: {oracle_mode!r}")

	# Reuse the live task session when it is available
	if context.runtime_session is not None and context.runtime_backend is not None:
		return cast(
			RuntimeCheckExecutor,
			SessionRuntimeCheckExecutor(
				session=context.runtime_session,
				runtime_backend=cast(BenchRuntime, context.runtime_backend),
				path_mounts=path_mounts,
				default_cwd=context.workspace_dir,
			),
		)

	# Fall back to local checks when no task runtime was recorded
	if runtime_result is None:
		return cast(
			RuntimeCheckExecutor,
			LocalRuntimeCheckExecutor(default_cwd=context.workspace_dir),
		)

	# Reuse local checks when the task ran locally.
	if runtime_result.runtime.mode == RuntimeMode.LOCAL:
		return cast(
			RuntimeCheckExecutor,
			LocalRuntimeCheckExecutor(default_cwd=context.workspace_dir),
		)

	# Fail if unsupported mode
	if runtime_result.runtime.mode != RuntimeMode.DOCKER:
		raise RuntimeError(
			f"Cannot build oracle runtime executor: {runtime_result.runtime.mode} mode is unsuported."
		)

	image = runtime_result.runtime.saved_image or runtime_result.runtime.image

	# Fail if no Docker image to reuse
	if not image:
		raise RuntimeError(
			"Cannot build oracle runtime executor: inherited Docker runtime has no image."
		)

	# Recreate Docker checks from the recorded task image
	return cast(
		RuntimeCheckExecutor,
		DockerRuntimeCheckExecutor(
			image=image,
			path_mounts=path_mounts,
			default_cwd=context.workspace_dir,
		),
	)

