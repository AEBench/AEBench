"""Subprocess execution and output capture for oracle checks."""

from __future__ import annotations

import codecs
import dataclasses
import locale
import pathlib
import selectors
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO, cast

from ..constants import SUBPROCESS_WAIT_TIMEOUT

DEFAULT_MAX_CAPTURE_CHARS = 16_384
_TRUNCATION_SUFFIX = "\n... [output truncated]"


@dataclasses.dataclass(frozen=True, slots=True)
class ProcResult:
	"""Captured result of an oracle subprocess.

	Attributes:
		returncode: Process exit code, or None if the process timed out.
		stdout: Captured standard output.
		stderr: Captured standard error.
		timed_out: Whether the process exceeded its timeout.
	"""

	returncode: int | None
	stdout: str
	stderr: str
	timed_out: bool


def decode_text(value: bytes | str | None) -> str:
	"""Converts optional subprocess output to text.

	Invalid UTF-8 bytes are replaced so diagnostic output remains available.
	"""
	if value is None:
		return ""
	if isinstance(value, bytes):
		return value.decode("utf-8", errors="replace")
	return value


def truncate_text(text: str, max_chars: int) -> str:
	"""Limits captured text and marks output that was truncated."""
	if len(text) <= max_chars:
		return text
	return text[:max_chars] + _TRUNCATION_SUFFIX


def _notify_output(
	*,
	stdout: str,
	stderr: str,
	on_chunk: Callable[[str, str], None] | None,
) -> None:
	"""Forwards captured output to an optional observer."""
	if on_chunk is None:
		return
	if stdout:
		on_chunk("stdout", stdout)
	if stderr:
		on_chunk("stderr", stderr)


def proc_result_from_completed_process(
	result: subprocess.CompletedProcess[str],
	*,
	capture_limit_chars: int,
	on_chunk: Callable[[str, str], None] | None,
) -> ProcResult:
	"""Converts a completed subprocess result to the oracle result format.

	Args:
		result: Completed subprocess result.
		capture_limit_chars: Maximum characters retained per output stream.
		on_chunk: Optional callback that receives untruncated output.

	Returns:
		A normalized process result.
	"""
	stdout = result.stdout or ""
	stderr = result.stderr or ""

	_notify_output(
		stdout=stdout,
		stderr=stderr,
		on_chunk=on_chunk,
	)

	return ProcResult(
		returncode=result.returncode,
		stdout=truncate_text(stdout, capture_limit_chars),
		stderr=truncate_text(stderr, capture_limit_chars),
		timed_out=False,
	)


def proc_result_from_timeout(
	exc: subprocess.TimeoutExpired,
	*,
	capture_limit_chars: int,
	on_chunk: Callable[[str, str], None] | None,
) -> ProcResult:
	"""Converts a subprocess timeout to the oracle result format.

	Args:
		exc: Timeout exception containing any output captured before timeout.
		capture_limit_chars: Maximum characters retained per output stream.
		on_chunk: Optional callback that receives untruncated output.

	Returns:
		A normalized timed-out process result.
	"""
	stdout = decode_text(exc.stdout)
	stderr = decode_text(exc.stderr)

	_notify_output(
		stdout=stdout,
		stderr=stderr,
		on_chunk=on_chunk,
	)

	return ProcResult(
		returncode=None,
		stdout=truncate_text(stdout, capture_limit_chars),
		stderr=truncate_text(stderr, capture_limit_chars),
		timed_out=True,
	)


def stream_subprocess(
	proc: subprocess.Popen[bytes],
	*,
	timeout_seconds: float,
	on_chunk: Callable[[str, bytes], None],
	drain_after_kill: bool = False,
) -> tuple[int | None, bool]:
	"""Streams stdout and stderr until the process exits or times out.

	Both streams are read through a selector so neither pipe can block the
	other. On timeout, the process is killed and may be drained briefly to
	preserve output emitted immediately before termination.

	Args:
		proc: Process configured with binary stdout and stderr pipes.
		timeout_seconds: Maximum process execution time.
		on_chunk: Callback receiving a stream name and raw output bytes.
		drain_after_kill: Whether to read remaining output after termination.

	Returns:
		A pair containing the return code and timeout status. The return code
		is None when the process times out.
	"""
	stdout = proc.stdout
	stderr = proc.stderr
	assert stdout is not None
	assert stderr is not None

	selector = selectors.DefaultSelector()
	try:
		selector.register(stdout, selectors.EVENT_READ, data="stdout")
		selector.register(stderr, selectors.EVENT_READ, data="stderr")
		deadline = time.monotonic() + timeout_seconds
		timed_out = False

		def close_stream(stream: IO[bytes]) -> None:
			"""Unregisters and closes a stream using best-effort cleanup."""
			try:
				selector.unregister(stream)
			except Exception:
				pass
			try:
				stream.close()
			except Exception:
				pass

		while selector.get_map():
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				timed_out = True
				break

			# Use short polling intervals so the timeout remains responsive
			for key, _mask in selector.select(timeout=min(0.25, remaining)):
				stream = cast(IO[bytes], key.fileobj)
				chunk = stream.read(8192)
				if not chunk:
					close_stream(stream)
					continue
				on_chunk(cast(str, key.data), chunk)

		if timed_out:
			try:
				proc.kill()
			except Exception:
				pass

			# Some processes flush useful diagnostics while being terminated
			if drain_after_kill:
				drain_deadline = time.monotonic() + 1.0
				while selector.get_map() and time.monotonic() < drain_deadline:
					for key, _mask in selector.select(timeout=0.1):
						stream = cast(IO[bytes], key.fileobj)
						chunk = stream.read(8192)
						if not chunk:
							close_stream(stream)
							continue
						on_chunk(cast(str, key.data), chunk)

			# Collect the terminated child process exit; but preserve
			# output/reporting even if the process does not terminate promptly
			try:
				proc.wait(timeout=SUBPROCESS_WAIT_TIMEOUT)
			except Exception:
				pass
			return None, True

		try:
			return proc.wait(timeout=SUBPROCESS_WAIT_TIMEOUT), False
		except Exception:
			# The terminated process exist status may have already
			# been collected thru platform-specific behavior
			return proc.returncode, False
	finally:
		selector.close()


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
	"""Runs a subprocess while streaming and retaining bounded output.

	Output is decoded incrementally so multibyte characters that split across read
	boundaries are reconstructed correctly. Callbacks receive all decoded
	output, while the returned result retains at most <capture_limit_chars>
	characters from each stream to control output size.

	Args:
		cmd: Shell command or argument vector to execute.
		cwd: Process working directory.
		env: Complete environment passed to the process.
		timeout_seconds: Maximum process execution time.
		use_shell: Whether to execute the command through the system shell.
		capture_limit_chars: Maximum retained characters per output stream.
		drain_after_kill: Whether to read remaining output after a timeout.
		encoding: Output encoding, or the preferred system encoding by default.
		on_chunk: Optional callback receiving decoded output as it arrives.

	Returns:
		The captured process result.

	Raises:
		ValueError: If <capture_limit_chars> is not positive.
		OSError: If the process cannot be started.
	"""
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

	# Separate incremental decoders preserve state for each output stream
	codec_name = encoding or locale.getpreferredencoding(False) or "utf-8"
	stdout_decoder = codecs.getincrementaldecoder(codec_name)(errors="replace")
	stderr_decoder = codecs.getincrementaldecoder(codec_name)(errors="replace")

	stdout_parts: list[str] = []
	stderr_parts: list[str] = []
	stdout_len = 0
	stderr_len = 0
	stdout_overflow = False
	stderr_overflow = False

	def append_bounded(
		parts: list[str],
		current_len: int,
		text: str,
	) -> tuple[int, bool]:
		"""Appends text without exceeding the configured capture limit."""
		if current_len >= capture_limit_chars:
			return current_len, True

		remaining = capture_limit_chars - current_len
		if len(text) <= remaining:
			parts.append(text)
			return current_len + len(text), False

		parts.append(text[:remaining])
		return capture_limit_chars, True

	def handle_text(stream_name: str, text: str) -> None:
		"""Forwards decoded output and retains a bounded copy."""
		nonlocal stdout_len, stderr_len
		nonlocal stdout_overflow, stderr_overflow

		if not text:
			return

		if on_chunk is not None:
			on_chunk(stream_name, text)

		if stream_name == "stdout":
			stdout_len, overflowed = append_bounded(
				stdout_parts,
				stdout_len,
				text,
			)
			stdout_overflow = stdout_overflow or overflowed
		else:
			stderr_len, overflowed = append_bounded(
				stderr_parts,
				stderr_len,
				text,
			)
			stderr_overflow = stderr_overflow or overflowed

	def handle_raw(stream_name: str, raw: bytes) -> None:
		"""Decodes a raw stream chunk using the corresponding decoder."""
		decoder = stdout_decoder if stream_name == "stdout" else stderr_decoder
		handle_text(stream_name, decoder.decode(raw))

	returncode, timed_out = stream_subprocess(
		proc,
		timeout_seconds=float(timeout_seconds),
		on_chunk=handle_raw,
		drain_after_kill=drain_after_kill,
	)

	# Flush any incomplete decoder state after both streams close
	handle_text("stdout", stdout_decoder.decode(b"", final=True))
	handle_text("stderr", stderr_decoder.decode(b"", final=True))

	stdout = "".join(stdout_parts)
	stderr = "".join(stderr_parts)
	if stdout_overflow:
		stdout += _TRUNCATION_SUFFIX
	if stderr_overflow:
		stderr += _TRUNCATION_SUFFIX

	return ProcResult(
		returncode=returncode,
		stdout=stdout,
		stderr=stderr,
		timed_out=timed_out,
	)
