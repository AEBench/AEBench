"""A Python stand-in for the Rust shim, used to drive the broker in tests.

It speaks the same wire protocol the shim does, so it doubles as the reference
for what the Rust client must send.
"""

from __future__ import annotations

from pathlib import Path
from socket import AF_UNIX, SOCK_STREAM, socket
from types import TracebackType
from typing import IO, Sequence

from runtime.commands.protocol import (
	DecisionMessage,
	EndMessage,
	FrameKind,
	ProtocolError,
	encode_begin,
	encode_control,
	encode_frame,
	read_control,
)
from runtime.commands.types import CommandRequest


class FakeShim:
	"""Drives one command's worth of protocol traffic."""

	def __init__(self, socket_path: Path) -> None:
		"""Connects to a broker listening on ``socket_path``."""
		self._socket = socket(AF_UNIX, SOCK_STREAM)
		self._socket.connect(str(socket_path))
		self._reader: IO[bytes] = self._socket.makefile("rb")
		self.command_id: str | None = None

	def begin(
		self,
		argv: Sequence[str] = ("bash", "-lc", "true"),
		*,
		cwd: str = "/repo",
		pid: int | None = 4242,
		env_keys: Sequence[str] = ("PATH",),
	) -> DecisionMessage:
		"""Announces the invocation and returns the broker's verdict."""
		self._send(
			encode_begin(
				CommandRequest(argv=tuple(argv), cwd=cwd, pid=pid, env_keys=tuple(env_keys))
			)
		)
		decision = read_control(self._reader)
		if not isinstance(decision, DecisionMessage):
			raise ProtocolError(f"expected a decision, got {decision!r}")
		self.command_id = decision.command_id
		return decision

	def write_stdout(self, data: bytes) -> None:
		"""Streams a stdout chunk."""
		self._send(encode_frame(FrameKind.STDOUT, data))

	def write_stderr(self, data: bytes) -> None:
		"""Streams a stderr chunk."""
		self._send(encode_frame(FrameKind.STDERR, data))

	def end(self, *, exit_code: int | None = 0, signal: int | None = None) -> None:
		"""Reports the outcome. Nothing comes back."""
		if self.command_id is None:
			raise RuntimeError("begin() must be called first")
		self._send(encode_control(EndMessage(exit_code=exit_code, signal=signal)))

	def send_raw(self, payload: bytes) -> None:
		"""Sends arbitrary bytes, for exercising malformed-input handling."""
		self._send(payload)

	def close(self) -> None:
		"""Drops the connection without reporting an outcome."""
		try:
			self._reader.close()
		finally:
			self._socket.close()

	def _send(self, payload: bytes) -> None:
		self._socket.sendall(payload)

	def __enter__(self) -> "FakeShim":
		"""Enters a context that closes the connection on exit."""
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc: BaseException | None,
		traceback: TracebackType | None,
	) -> None:
		"""Closes the connection."""
		self.close()


__all__ = ["FakeShim"]
