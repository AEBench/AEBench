"""Oracle check primitives and subprocess helpers."""

from __future__ import annotations

import abc
import codecs
import dataclasses
import enum
import locale
import logging
import os
import pathlib
import selectors
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any, Protocol, cast, runtime_checkable

from models import OracleInput, RuntimeMode
from runtime.backend import BenchRuntime, validate_env_var_name

from ..constants import SUBPROCESS_WAIT_TIMEOUT

DEFAULT_MAX_CAPTURE_CHARS: int = 16_384
DEFAULT_MAX_TRUNCATED_MESSAGE_CHARS: int = 2_048
_DEFAULT_TRUNCATION_SUFFIX = "\n... [output truncated]"


def decode_text(value: bytes | str | None) -> str:
	if value is None:
		return ""
	if isinstance(value, bytes):
		return value.decode("utf-8", errors="replace")
	return value


def truncate_text(text: str, max_chars: int) -> str:
	if len(text) <= max_chars:
		return text
	return text[:max_chars] + "\n... [output truncated]"


class CheckOutcome(enum.Enum):
	PASSED = "passed"
	FAILED = "failed"
	WARNING = "warning"


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:
	ok: bool
	message: str
	stdout: str = ""
	stderr: str = ""
	returncode: int | None = None
	timed_out: bool = False
	cwd: pathlib.Path | None = None

	@classmethod
	def success(
		cls,
		message: str = "",
		*,
		stdout: str = "",
		stderr: str = "",
		returncode: int | None = None,
		timed_out: bool = False,
		cwd: pathlib.Path | None = None,
	) -> "CheckResult":
		return cls(
			ok=True,
			message=message,
			stdout=stdout,
			stderr=stderr,
			returncode=returncode,
			timed_out=timed_out,
			cwd=cwd,
		)

	@classmethod
	def failure(
		cls,
		message: str,
		*,
		stdout: str = "",
		stderr: str = "",
		returncode: int | None = None,
		timed_out: bool = False,
		cwd: pathlib.Path | None = None,
	) -> "CheckResult":
		return cls(
			ok=False,
			message=message,
			stdout=stdout,
			stderr=stderr,
			returncode=returncode,
			timed_out=timed_out,
			cwd=cwd,
		)


@runtime_checkable
class Checkable(Protocol):
	"""Structural type for anything that produces a check result."""

	@property
	def name(self) -> str:
		raise NotImplementedError

	@property
	def optional(self) -> bool:
		raise NotImplementedError

	def check(self) -> CheckResult:
		raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True)
class CheckEntry:
	name: str
	outcome: CheckOutcome
	message: str
	optional: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class OracleReport:
	results: tuple[CheckEntry, ...]

	@property
	def ok(self) -> bool:
		return all(r.outcome != CheckOutcome.FAILED for r in self.results if not r.optional)

	@property
	def passed_count(self) -> int:
		return sum(1 for r in self.results if r.outcome == CheckOutcome.PASSED)

	@property
	def failed_count(self) -> int:
		return sum(1 for r in self.results if r.outcome == CheckOutcome.FAILED)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BaseCheck(Checkable, abc.ABC):
	name: str
	optional: bool = False

	@abc.abstractmethod
	def check(self) -> CheckResult:
		raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Check(BaseCheck):
	"""Function-backed check: wraps a callable into a BaseCheck."""

	fn: Callable[[], CheckResult]

	def check(self) -> CheckResult:
		return self.fn()


@dataclasses.dataclass(frozen=True, slots=True)
class ProcResult:
	returncode: int | None
	stdout: str
	stderr: str
	timed_out: bool


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

	def close(self) -> None:
		raise NotImplementedError


def _truncate_capture(text: str, *, limit: int) -> str:
	if len(text) <= limit:
		return text
	return text[:limit] + _DEFAULT_TRUNCATION_SUFFIX


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
		merged_env = os.environ.copy()
		merged_env.update(env)
		run_env = merged_env
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


class LocalRuntimeCheckExecutor:
	path_separator = os.pathsep

	def __init__(self, *, default_cwd: pathlib.Path) -> None:
		self._default_cwd = default_cwd

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


class SessionRuntimeCheckExecutor:
	def __init__(
		self,
		*,
		session: Any,
		runtime_backend: BenchRuntime,
	) -> None:
		self._session = session
		self._runtime_backend = runtime_backend

	@property
	def path_separator(self) -> str:
		return self._runtime_backend.path_separator

	def _translate_cwd(self, cwd: pathlib.Path | None) -> str | None:
		translate = getattr(self._session, "translate_host_path", None)
		if callable(translate):
			return cast(str | None, translate(cwd))
		if cwd is None:
			return None
		return str(cwd)

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
		run_cmd: list[str]
		if use_shell:
			if isinstance(cmd, str):
				run_cmd = ["sh", "-lc", cmd]
			else:
				run_cmd = ["sh", "-lc", " ".join(shlex.quote(part) for part in cmd)]
		else:
			if isinstance(cmd, str):
				raise TypeError(
					"run_process_capture expected a sequence cmd when use_shell = False"
				)
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
				stdout=_truncate_capture(stdout, limit=capture_limit_chars),
				stderr=_truncate_capture(stderr, limit=capture_limit_chars),
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
			stdout=_truncate_capture(stdout, limit=capture_limit_chars),
			stderr=_truncate_capture(stderr, limit=capture_limit_chars),
			timed_out=False,
		)

	def close(self) -> None:
		return None


class DockerRuntimeCheckExecutor:
	path_separator = ":"

	def __init__(
		self,
		*,
		default_cwd: pathlib.Path,
		workspace_dir: pathlib.Path,
		refs_dir: pathlib.Path | None,
		image: str,
	) -> None:
		self._default_cwd = default_cwd
		self._workspace_dir = workspace_dir.resolve()
		self._refs_dir = None if refs_dir is None else refs_dir.resolve()
		self._image = image
		self._container_name = f"aebench-oracle-{int(time.time() * 1000)}"
		self._container_id: str | None = None

	def _ensure_container(self) -> str:
		if self._container_id is not None:
			return self._container_id

		cmd = [
			"docker",
			"run",
			"-d",
			"--init",
			"--name",
			self._container_name,
			"-v",
			f"{self._workspace_dir}:/repo",
			"-w",
			"/repo",
		]
		if self._refs_dir is not None and self._refs_dir.exists():
			cmd.extend(["-v", f"{self._refs_dir}:/refs:ro"])
		cmd.extend([self._image, "sleep", "infinity"])

		result = subprocess.run(cmd, capture_output=True, text=True, check=False)
		if result.returncode != 0:
			detail = (result.stderr or result.stdout).strip() or "docker run failed"
			raise RuntimeError(detail)
		self._container_id = result.stdout.strip()
		return self._container_id

	def _translate_cwd(self, cwd: pathlib.Path | None) -> str | None:
		target = (cwd or self._default_cwd).resolve()
		if target == self._workspace_dir:
			return "/repo"
		try:
			relative = target.relative_to(self._workspace_dir)
			return f"/repo/{relative.as_posix()}"
		except ValueError:
			pass

		if self._refs_dir is not None:
			if target == self._refs_dir:
				return "/refs"
			try:
				relative = target.relative_to(self._refs_dir)
				return f"/refs/{relative.as_posix()}"
			except ValueError:
				pass
		return str(target)

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
		translated_cwd = self._translate_cwd(cwd)
		if translated_cwd:
			docker_cmd.extend(["-w", translated_cwd])
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
				cwd=self._default_cwd,
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
				cwd=self._default_cwd,
				env=env,
				timeout_seconds=5.0,
			)
		except (OSError, RuntimeError, subprocess.TimeoutExpired):
			return None
		if result.returncode != 0:
			return None
		return result.stdout.removesuffix("\n")

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
		try:
			if use_shell:
				if not isinstance(cmd, str):
					raise TypeError(
						"run_process_capture expected a string cmd when use_shell = True"
					)
				result = self._docker_exec(
					cmd=["sh", "-lc", cmd],
					cwd=cwd,
					env=env,
					timeout_seconds=timeout_seconds,
				)
			else:
				if isinstance(cmd, str):
					raise TypeError(
						"run_process_capture expected a sequence cmd when use_shell = False"
					)
				result = self._docker_exec(
					cmd=list(cmd),
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
				stdout=_truncate_capture(stdout, limit=capture_limit_chars),
				stderr=_truncate_capture(stderr, limit=capture_limit_chars),
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
			stdout=_truncate_capture(stdout, limit=capture_limit_chars),
			stderr=_truncate_capture(stderr, limit=capture_limit_chars),
			timed_out=False,
		)

	def close(self) -> None:
		target = self._container_id or self._container_name
		if not target:
			return
		subprocess.run(
			["docker", "rm", "-f", target],
			capture_output=True,
			text=True,
			check=False,
		)
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

	def close(self) -> None:
		return None


def build_runtime_check_executor(context: OracleInput) -> RuntimeCheckExecutor:
	runtime_result = context.runtime_result
	saved_image = (
		runtime_result.runtime.saved_image
		if runtime_result is not None and runtime_result.runtime.mode == RuntimeMode.DOCKER
		else None
	)
	if saved_image is not None:
		refs_dir = context.case_dir / "refs"
		return cast(
			RuntimeCheckExecutor,
			DockerRuntimeCheckExecutor(
				default_cwd=context.workspace_dir,
				workspace_dir=context.workspace_dir,
				refs_dir=refs_dir if refs_dir.exists() else None,
				image=saved_image,
			),
		)

	if context.runtime_session is not None and context.runtime_backend is not None:
		return cast(
			RuntimeCheckExecutor,
			SessionRuntimeCheckExecutor(
				session=context.runtime_session,
				runtime_backend=cast(BenchRuntime, context.runtime_backend),
			),
		)

	if runtime_result is None or runtime_result.runtime.mode == RuntimeMode.LOCAL:
		return cast(
			RuntimeCheckExecutor,
			LocalRuntimeCheckExecutor(default_cwd=context.workspace_dir),
		)

	image = runtime_result.runtime.image
	if not image:
		return cast(
			RuntimeCheckExecutor,
			UnavailableRuntimeCheckExecutor(
				message="docker oracle checks require runtime.image to be set",
			),
		)

	refs_dir = context.case_dir / "refs"
	return cast(
		RuntimeCheckExecutor,
		DockerRuntimeCheckExecutor(
			default_cwd=context.workspace_dir,
			workspace_dir=context.workspace_dir,
			refs_dir=refs_dir if refs_dir.exists() else None,
			image=image,
		),
	)


def stream_subprocess(
	proc: subprocess.Popen[bytes],
	*,
	timeout_seconds: float,
	on_chunk: Callable[[str, bytes], None],
	drain_after_kill: bool = False,
) -> tuple[int | None, bool]:
	"""Drive proc to completion, calling on_chunk per chunk, handle timeout/kill."""
	stdout = proc.stdout
	stderr = proc.stderr
	assert stdout is not None
	assert stderr is not None

	sel = selectors.DefaultSelector()
	try:
		sel.register(stdout, selectors.EVENT_READ, data="stdout")
		sel.register(stderr, selectors.EVENT_READ, data="stderr")
		stdout_stream: IO[bytes] = stdout
		stderr_stream: IO[bytes] = stderr

		deadline = time.monotonic() + timeout_seconds
		timed_out = False

		def _read_chunk(stream: IO[bytes]) -> bytes:
			return stream.read(8192)

		def _close_stream(stream: IO[bytes]) -> None:
			try:
				sel.unregister(stream)
			except Exception:
				pass
			try:
				stream.close()
			except Exception:
				pass

		def _drain(drain_seconds: float) -> None:
			drain_deadline = time.monotonic() + drain_seconds
			while sel.get_map() and time.monotonic() < drain_deadline:
				for key, _mask in sel.select(timeout=0.1):
					stream = stdout_stream if key.fileobj is stdout_stream else stderr_stream
					chunk = _read_chunk(stream)
					if not chunk:
						_close_stream(stream)
						continue
					on_chunk(key.data, chunk)

		while sel.get_map():
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				timed_out = True
				break
			for key, _mask in sel.select(timeout=min(0.25, remaining)):
				stream = stdout if key.fileobj is stdout else stderr
				chunk = _read_chunk(stream)
				if not chunk:
					_close_stream(stream)
					continue
				on_chunk(key.data, chunk)

		if timed_out:
			try:
				proc.kill()
			except Exception:
				pass
			if drain_after_kill:
				_drain(1.0)
			try:
				proc.wait(timeout=SUBPROCESS_WAIT_TIMEOUT)
			except Exception:
				pass
			return None, True

		try:
			rc = proc.wait(timeout=SUBPROCESS_WAIT_TIMEOUT)
		except Exception:
			rc = proc.returncode
		return rc, False
	finally:
		sel.close()


def run_subprocess_capture(
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
	"""Run a subprocess with bounded stdout/stderr capture."""
	if capture_limit_chars <= 0:
		raise ValueError("capture_limit_chars must be > 0")

	proc = subprocess.Popen(
		cmd,
		cwd=cwd,
		env=dict(env) if env is not None else None,
		stdin=subprocess.DEVNULL,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		shell=use_shell,
		text=False,
	)

	codec_name = encoding or locale.getpreferredencoding(False) or "utf-8"
	stdout_decoder = codecs.getincrementaldecoder(codec_name)(errors="replace")
	stderr_decoder = codecs.getincrementaldecoder(codec_name)(errors="replace")

	stdout_parts: list[str] = []
	stderr_parts: list[str] = []
	stdout_len = 0
	stderr_len = 0
	stdout_overflow = False
	stderr_overflow = False

	def _append_bounded(parts: list[str], current_len: int, text: str) -> tuple[int, bool]:
		if current_len >= capture_limit_chars:
			return current_len, True
		remaining = capture_limit_chars - current_len
		if len(text) <= remaining:
			parts.append(text)
			return current_len + len(text), False
		parts.append(text[:remaining])
		return capture_limit_chars, True

	def _handle_text(stream_name: str, text: str) -> None:
		nonlocal stdout_len, stderr_len, stdout_overflow, stderr_overflow
		if not text:
			return
		if on_chunk is not None:
			on_chunk(stream_name, text)

		if stream_name == "stdout":
			stdout_len, overflowed = _append_bounded(stdout_parts, stdout_len, text)
			stdout_overflow = stdout_overflow or overflowed
		else:
			stderr_len, overflowed = _append_bounded(stderr_parts, stderr_len, text)
			stderr_overflow = stderr_overflow or overflowed

	def _on_raw_chunk(stream_name: str, raw: bytes) -> None:
		decoder = stdout_decoder if stream_name == "stdout" else stderr_decoder
		_handle_text(stream_name, decoder.decode(raw))

	returncode, timed_out = stream_subprocess(
		proc,
		timeout_seconds=float(timeout_seconds),
		on_chunk=_on_raw_chunk,
		drain_after_kill=drain_after_kill,
	)

	_handle_text("stdout", stdout_decoder.decode(b"", final=True))
	_handle_text("stderr", stderr_decoder.decode(b"", final=True))

	stdout = "".join(stdout_parts)
	stderr = "".join(stderr_parts)
	if stdout_overflow:
		stdout += _DEFAULT_TRUNCATION_SUFFIX
	if stderr_overflow:
		stderr += _DEFAULT_TRUNCATION_SUFFIX

	return ProcResult(
		returncode=returncode,
		stdout=stdout,
		stderr=stderr,
		timed_out=timed_out,
	)


def run_checks(checks: Sequence[Checkable], *, logger: logging.Logger) -> OracleReport:
	"""Run each check and build a report."""
	results: list[CheckEntry] = []
	for req in checks:
		try:
			result = req.check()
			if result.ok:
				outcome = CheckOutcome.PASSED
				msg = result.message or "ok"
			else:
				outcome = CheckOutcome.WARNING if req.optional else CheckOutcome.FAILED
				msg = result.message or "failed"
		except Exception as exc:
			outcome = CheckOutcome.WARNING if req.optional else CheckOutcome.FAILED
			msg = f"unexpected error: {type(exc).__name__}: {exc}"
			logger.exception("check %r raised an unexpected exception", req.name)

		results.append(
			CheckEntry(
				name=req.name,
				outcome=outcome,
				message=msg,
				optional=req.optional,
			)
		)
	return OracleReport(results=tuple(results))


def build_oracle_report(
	*,
	logger: logging.Logger,
	requirements: Callable[[], Sequence[BaseCheck]],
) -> OracleReport:
	"""Enumerate requirements and run checks. Used by class-based _OraclePhaseBase.report()."""
	try:
		reqs = requirements()
	except Exception as exc:
		msg = f"failed to enumerate requirements: {type(exc).__name__}: {exc}"
		logger.error(msg)
		return OracleReport(
			results=(
				CheckEntry(
					name="<requirements>",
					outcome=CheckOutcome.FAILED,
					message=msg,
					optional=False,
				),
			)
		)
	return run_checks(reqs, logger=logger)


def log_oracle_report(
	logger: logging.Logger,
	*,
	label: str,
	report: OracleReport,
	verbose: bool = False,
) -> bool:
	for result in report.results:
		if result.outcome == CheckOutcome.PASSED:
			if verbose:
				logger.info("[%s] %s: PASSED: %s", label, result.name, result.message)
		elif result.outcome == CheckOutcome.WARNING:
			logger.warning("[%s] %s: WARNING: %s", label, result.name, result.message)
		else:
			logger.error("[%s] %s: FAILED: %s", label, result.name, result.message)
	return report.ok


class _OraclePhaseBase(abc.ABC):
	"""Shared base for all four oracle phase types."""

	phase_label = "OraclePhase"

	def __init__(self, *, logger: logging.Logger, **_kwargs: OracleInput) -> None:
		super().__init__()
		self._logger = logger

	@abc.abstractmethod
	def requirements(self) -> Sequence[BaseCheck]:
		raise NotImplementedError

	def report(self) -> OracleReport:
		return build_oracle_report(
			logger=self._logger,
			requirements=self.requirements,
		)

	def run(self, *, verbose: bool = False) -> bool:
		rep = self.report()
		return log_oracle_report(
			self._logger,
			label=self.phase_label,
			report=rep,
			verbose=verbose,
		)
