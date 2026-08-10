"""Unix-socket server that decides on and records monitored commands.

One broker runs per agent invocation. Connections are long-lived — a shim holds
one open for the whole lifetime of its command — so every connection gets its
own thread. A single-connection server would deadlock the first parallel build:
each recipe line execs a shim, the children block on ``accept``, and the parent
waits on the children.

Nesting is derived here rather than reported by the shim. The broker knows the
pid behind every live connection, so it walks ``/proc`` upward from a new shim
until it meets one of them. That crosses intermediate non-shell processes — the
parent of a shim inside a ``make`` recipe is ``make``, not the outer shim — and
it keeps the shim free of state. Linux only, and it relies on the broker
sharing a pid namespace with the agent, which holds for local runs.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from .protocol import (
	DecisionMessage,
	EndMessage,
	FrameKind,
	ProtocolError,
	decode_control,
	encode_control,
	read_control,
	read_frame,
)
from .runner import CommandRunner, Verdict
from .trace import CaptureSink, CommandTraceWriter
from .types import CaptureState, CommandOutcome, CommandRecord, CommandRequest

logger = logging.getLogger(__name__)

DEFAULT_BACKLOG = 128
SOCKET_BASENAME = "command.sock"

#: Give up walking ancestors rather than looping on a pathological chain.
_MAX_ANCESTOR_DEPTH = 64


class CommandBroker:
	"""Accepts shim connections and turns them into trace records."""

	def __init__(
		self,
		*,
		socket_path: Path,
		runner: CommandRunner,
		trace: CommandTraceWriter,
		backlog: int = DEFAULT_BACKLOG,
	) -> None:
		"""Initializes a broker bound to ``socket_path``."""
		self._socket_path = Path(socket_path).expanduser()
		self._runner = runner
		self._trace = trace
		self._backlog = backlog
		self._server: socket.socket | None = None
		self._accept_thread: threading.Thread | None = None
		self._workers: set[threading.Thread] = set()
		self._workers_lock = threading.Lock()
		self._live: dict[int, str] = {}
		self._live_lock = threading.Lock()
		self._closing = threading.Event()

	@property
	def socket_path(self) -> Path:
		"""Returns the bound socket path."""
		return self._socket_path

	def start(self) -> None:
		"""Binds the socket and starts accepting connections."""
		if self._server is not None:
			raise RuntimeError("broker is already started")

		self._socket_path.parent.mkdir(parents=True, exist_ok=True)
		# A stale socket from a crashed run would make bind() fail with EADDRINUSE.
		if self._socket_path.exists() or self._socket_path.is_symlink():
			self._socket_path.unlink()

		server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		try:
			server.bind(str(self._socket_path))
			os.chmod(self._socket_path, 0o600)
			server.listen(self._backlog)
		except BaseException:
			server.close()
			raise

		self._server = server
		self._closing.clear()
		self._accept_thread = threading.Thread(
			target=self._accept_loop, args=(server,), name="aebench-command-broker", daemon=True
		)
		self._accept_thread.start()
		logger.info("command broker listening on %s", self._socket_path)

	def stop(self, *, timeout: float = 5.0) -> None:
		"""Stops accepting and waits briefly for in-flight connections.

		In-flight commands are not killed. A shim serving a long build keeps its
		thread; the process is exiting anyway and the threads are daemons.
		"""
		self._closing.set()

		server, self._server = self._server, None
		if server is not None:
			try:
				server.shutdown(socket.SHUT_RDWR)
			except OSError:
				pass
			server.close()

		if self._accept_thread is not None:
			self._accept_thread.join(timeout=timeout)
			self._accept_thread = None

		with self._workers_lock:
			workers = list(self._workers)
		for worker in workers:
			worker.join(timeout=timeout)

		try:
			self._socket_path.unlink()
		except FileNotFoundError:
			pass
		except OSError:
			logger.warning("failed to remove command socket %s", self._socket_path)

	def __enter__(self) -> "CommandBroker":
		"""Starts the broker."""
		self.start()
		return self

	def __exit__(self, *_exc: object) -> None:
		"""Stops the broker."""
		self.stop()

	def _accept_loop(self, server: socket.socket) -> None:
		while not self._closing.is_set():
			try:
				connection, _ = server.accept()
			except OSError:
				if not self._closing.is_set():
					logger.exception("command broker accept failed")
				return

			worker = threading.Thread(
				target=self._serve, args=(connection,), name="aebench-command", daemon=True
			)
			with self._workers_lock:
				self._workers = {existing for existing in self._workers if existing.is_alive()}
				self._workers.add(worker)
			worker.start()

	def _serve(self, connection: socket.socket) -> None:
		try:
			with connection, connection.makefile("rb") as reader:
				self._handle(connection, reader)
		except ProtocolError as exc:
			logger.warning("rejecting malformed shim connection: %s", exc)
		except OSError as exc:
			logger.warning("command connection failed: %s", exc)
		except Exception:
			logger.exception("unhandled error while serving a command connection")

	def _handle(self, connection: socket.socket, reader: IO[bytes]) -> None:
		begin = read_control(reader)
		if begin is None:
			return
		if not isinstance(begin, CommandRequest):
			raise ProtocolError(f"expected begin, got {type(begin).__name__}")

		command_id = self._trace.allocate_command_id()
		request = dataclasses.replace(begin, parent_command_id=self._parent_of(begin.pid))
		verdict = self._runner.decide(request)
		started_at = _now()

		connection.sendall(
			encode_control(
				DecisionMessage(
					command_id=command_id,
					allow=verdict.decision.allow,
					reason=verdict.decision.reason,
					exit_code=verdict.decision.exit_code,
				)
			)
		)

		if not verdict.decision.allow:
			self._publish(
				CommandRecord(
					command_id=command_id,
					request=request,
					decision=verdict.decision,
					outcome=CommandOutcome.denied(verdict.decision),
					started_at=started_at,
					finished_at=started_at,
					denied_by=verdict.denied_by,
				)
			)
			return

		self._register(begin.pid, command_id)
		try:
			self._collect(
				reader,
				request=request,
				verdict=verdict,
				command_id=command_id,
				started_at=started_at,
			)
		finally:
			self._unregister(begin.pid)

	def _collect(
		self,
		reader: IO[bytes],
		*,
		request: CommandRequest,
		verdict: Verdict,
		command_id: str,
		started_at: datetime,
	) -> None:
		"""Reads the command's buffered output, then its outcome."""
		stdout_sink = self._trace.open_capture(command_id, "stdout")
		stderr_sink = self._trace.open_capture(command_id, "stderr")
		end: EndMessage | None = None

		try:
			while True:
				frame = read_frame(reader)
				if frame is None:
					break
				if frame.kind is FrameKind.STDOUT:
					stdout_sink.write(frame.payload)
					continue
				if frame.kind is FrameKind.STDERR:
					stderr_sink.write(frame.payload)
					continue

				message = decode_control(frame.payload)
				if not isinstance(message, EndMessage):
					raise ProtocolError(f"expected end, got {type(message).__name__}")
				end = message
				break
		finally:
			stdout_sink.close()
			stderr_sink.close()

		finished_at = _now()
		outcome = CommandOutcome(
			exit_code=None if end is None else end.exit_code,
			signal=None if end is None else end.signal,
			duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
			stdout_state=_capture_state(stdout_sink),
			stderr_state=_capture_state(stderr_sink),
		)
		self._publish(
			CommandRecord(
				command_id=command_id,
				request=request,
				decision=verdict.decision,
				outcome=outcome,
				started_at=started_at,
				finished_at=finished_at,
				stdout_path=self._trace.relative_capture_path(stdout_sink.path),
				stderr_path=self._trace.relative_capture_path(stderr_sink.path),
				incomplete=end is None,
			)
		)

	def _register(self, pid: int | None, command_id: str) -> None:
		if pid is None:
			return
		with self._live_lock:
			self._live[pid] = command_id

	def _unregister(self, pid: int | None) -> None:
		if pid is None:
			return
		with self._live_lock:
			self._live.pop(pid, None)

	def _parent_of(self, pid: int | None) -> str | None:
		"""Returns the command id of the nearest ancestor being monitored."""
		if pid is None:
			return None

		current = _parent_pid(pid)
		for _ in range(_MAX_ANCESTOR_DEPTH):
			if current is None or current <= 1:
				return None
			with self._live_lock:
				command_id = self._live.get(current)
			if command_id is not None:
				return command_id
			current = _parent_pid(current)
		return None

	def _publish(self, record: CommandRecord) -> None:
		self._trace.write(record)
		self._runner.observe(record)


def _parent_pid(pid: int) -> int | None:
	"""Reads a process's parent pid from ``/proc``."""
	try:
		status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
	except OSError:
		return None
	for line in status.splitlines():
		if line.startswith("PPid:"):
			try:
				return int(line.split()[1])
			except (IndexError, ValueError):
				return None
	return None


def _capture_state(sink: CaptureSink) -> CaptureState:
	return CaptureState.CAPTURED if sink.path is not None else CaptureState.NOT_CAPTURED


def _now() -> datetime:
	return datetime.now(timezone.utc)


__all__ = ["DEFAULT_BACKLOG", "SOCKET_BASENAME", "CommandBroker"]
