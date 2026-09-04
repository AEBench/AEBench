"""A fake command broker used to drive the real ``aeshell`` binary.

The shim is the component under test, so these tests deliberately do not talk
to ``monitor.py``: a failure here must implicate the shim, never the real
broker. This is the smallest server that speaks the shim's protocol correctly,
records every frame it receives, and lets a test choose the verdict.

Wire format, as implemented in ``src/runtime/shim/src/main.rs``::

    u32 payload_length (big endian) | u8 frame_kind | payload

One shell invocation is one connection::

    shim -> command_info
         <- decision
    shim -> stdout / stderr        (once, after the child has exited)
    shim -> end
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import IO, Any

COMMAND_INFO = 0
STDOUT = 1
STDERR = 2
END = 3
DECISION = 4

FRAME_NAMES = {
	COMMAND_INFO: "command_info",
	STDOUT: "stdout",
	STDERR: "stderr",
	END: "end",
	DECISION: "decision",
}

_HEADER = struct.Struct(">IB")

#: How long a test waits for the shim to finish talking before giving up.
DEFAULT_TIMEOUT = 30.0


class BrokerError(RuntimeError):
	"""Raised when the shim sends something the protocol does not allow."""


def encode_frame(kind: int, payload: bytes) -> bytes:
	"""Returns one wire frame carrying ``payload``."""
	return _HEADER.pack(len(payload), kind) + payload


def _read_exactly(reader: IO[bytes], size: int) -> bytes | None:
	"""Reads exactly ``size`` bytes, or ``None`` at a clean end of stream."""
	data = bytearray()
	while len(data) < size:
		chunk = reader.read(size - len(data))
		if not chunk:
			if not data:
				return None
			raise BrokerError("connection ended mid-frame")
		data.extend(chunk)
	return bytes(data)


def read_frame(reader: IO[bytes]) -> tuple[int, bytes] | None:
	"""Reads one frame, or ``None`` once the shim has closed the connection."""
	header = _read_exactly(reader, _HEADER.size)
	if header is None:
		return None
	length, kind = _HEADER.unpack(header)
	if kind not in FRAME_NAMES:
		raise BrokerError(f"unknown frame kind {kind}")
	payload = b"" if length == 0 else _read_exactly(reader, length)
	if payload is None:
		raise BrokerError("connection ended mid-frame")
	return kind, payload


@dataclass
class Decision:
	"""The verdict this broker returns for every command it is asked about."""

	allow: bool = True
	reason: str = ""
	exit_code: int = 126
	command_id: str = "cmd-0001"

	def payload(self) -> bytes:
		"""Returns the JSON body of the ``decision`` frame."""
		return json.dumps(
			{
				"command_id": self.command_id,
				"allow": self.allow,
				"reason": self.reason,
				"exit_code": self.exit_code,
			}
		).encode("utf-8")


@dataclass
class Session:
	"""Everything one shim connection sent, in the order it arrived."""

	frames: list[tuple[int, bytes]] = field(default_factory=list)
	error: str | None = None
	finished: threading.Event = field(default_factory=threading.Event)

	@property
	def kinds(self) -> list[int]:
		"""Returns the frame kinds received, in order."""
		return [kind for kind, _ in self.frames]

	@property
	def kind_names(self) -> list[str]:
		"""Returns the frame kinds received as names, in order."""
		return [FRAME_NAMES[kind] for kind in self.kinds]

	def payloads(self, kind: int) -> list[bytes]:
		"""Returns every payload received for one frame kind."""
		return [payload for received, payload in self.frames if received == kind]

	@property
	def command_info(self) -> dict[str, Any]:
		"""Returns the decoded ``command_info`` body."""
		payloads = self.payloads(COMMAND_INFO)
		if not payloads:
			raise BrokerError("no command_info frame was received")
		decoded: dict[str, Any] = json.loads(payloads[0])
		return decoded

	@property
	def stdout(self) -> bytes:
		"""Returns the captured stdout, concatenated across frames."""
		return b"".join(self.payloads(STDOUT))

	@property
	def stderr(self) -> bytes:
		"""Returns the captured stderr, concatenated across frames."""
		return b"".join(self.payloads(STDERR))

	@property
	def end(self) -> dict[str, Any] | None:
		"""Returns the decoded ``end`` body, or ``None`` if it never arrived."""
		payloads = self.payloads(END)
		if not payloads:
			return None
		decoded: dict[str, Any] = json.loads(payloads[0])
		return decoded


class FakeBroker:
	"""A broker that listens on a unix socket and records what the shim says.

	It is started before it is handed to a test, so the socket already exists
	and no readiness sleep is needed. Every connection is served on its own
	thread, the way a real broker serves nested shells.
	"""

	def __init__(self, socket_path: Path, decision: Decision | None = None) -> None:
		"""Prepares a broker that will listen on ``socket_path``."""
		self.socket_path = socket_path
		self.decision = decision or Decision()
		self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		self._sessions: list[Session] = []
		self._condition = threading.Condition()
		self._workers: list[threading.Thread] = []
		self._accept_thread: threading.Thread | None = None
		self._stopping = False

	def start(self) -> "FakeBroker":
		"""Binds, listens, and begins accepting. Returns once the socket exists."""
		self.socket_path.parent.mkdir(parents=True, exist_ok=True)
		self._server.bind(str(self.socket_path))
		self._server.listen(16)
		self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
		self._accept_thread.start()
		return self

	def stop(self) -> None:
		"""Stops accepting and waits for the connections already in flight."""
		self._stopping = True
		self._server.close()
		if self._accept_thread is not None:
			self._accept_thread.join(timeout=5.0)
		for worker in list(self._workers):
			worker.join(timeout=5.0)
		self.socket_path.unlink(missing_ok=True)

	@property
	def sessions(self) -> list[Session]:
		"""Returns the sessions seen so far, in connection order."""
		with self._condition:
			return list(self._sessions)

	def wait_for_session(self, index: int = 0, timeout: float = DEFAULT_TIMEOUT) -> Session:
		"""Waits for connection ``index`` to arrive and finish, then returns it."""
		deadline = timeout
		with self._condition:
			while len(self._sessions) <= index:
				if not self._condition.wait(timeout=deadline):
					raise AssertionError(
						f"the shim opened {len(self._sessions)} connection(s); "
						f"expected at least {index + 1}"
					)
			session = self._sessions[index]
		if not session.finished.wait(timeout=timeout):
			raise AssertionError(
				f"connection {index} never finished; frames so far: {session.kind_names}"
			)
		return session

	def _accept_loop(self) -> None:
		while not self._stopping:
			try:
				connection, _ = self._server.accept()
			except OSError:
				return
			worker = threading.Thread(target=self._serve, args=(connection,), daemon=True)
			self._workers.append(worker)
			worker.start()

	def _serve(self, connection: socket.socket) -> None:
		session = Session()
		with self._condition:
			self._sessions.append(session)
			self._condition.notify_all()
		try:
			with connection, connection.makefile("rb") as reader:
				while True:
					frame = read_frame(reader)
					if frame is None:
						return
					session.frames.append(frame)
					if frame[0] == COMMAND_INFO:
						connection.sendall(encode_frame(DECISION, self.decision.payload()))
		except Exception as exc:
			session.error = f"{type(exc).__name__}: {exc}"
		finally:
			session.finished.set()

	def __enter__(self) -> "FakeBroker":
		"""Enters a context that stops the broker on exit."""
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc: BaseException | None,
		traceback: TracebackType | None,
	) -> None:
		"""Stops the broker."""
		self.stop()


__all__ = [
	"COMMAND_INFO",
	"DECISION",
	"END",
	"FRAME_NAMES",
	"STDERR",
	"STDOUT",
	"BrokerError",
	"Decision",
	"FakeBroker",
	"Session",
	"encode_frame",
	"read_frame",
]
