"""The broker supervisor driving the real monitor.py over a unix socket.

No Docker and no Rust binary: a Python client speaks the shim's wire protocol,
so this exercises the supervisor and the broker exactly as a run would.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from pathlib import Path

import pytest

from runtime.monitoring import BrokerProcess

pytestmark = pytest.mark.sanity

_HEADER = struct.Struct(">IB")
_COMMAND_INFO = 0
_STDOUT = 1
_END = 3
_DECISION = 4


def _frame(kind: int, payload: bytes) -> bytes:
	return _HEADER.pack(len(payload), kind) + payload


def _wait_for(predicate: object, *, timeout: float = 10.0) -> bool:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if callable(predicate) and predicate():
			return True
		time.sleep(0.02)
	return False


def _run_one_command(socket_path: Path, argv: list[str]) -> None:
	"""Plays one command through the protocol the way the shim does."""
	client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	client.connect(str(socket_path))
	try:
		info = {"argv": argv, "cwd": "/repo", "pid": os.getpid(), "env_keys": ["PATH"]}
		client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))

		header = client.recv(_HEADER.size)
		length, kind = _HEADER.unpack(header)
		decision = json.loads(client.recv(length))
		assert kind == _DECISION
		assert decision["allow"] is True

		client.sendall(_frame(_STDOUT, b"hello\n"))
		client.sendall(_frame(_END, json.dumps({"exit_code": 0, "signal": None}).encode("utf-8")))
	finally:
		client.close()


@pytest.fixture(name="broker")
def _broker(tmp_path: Path) -> object:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=workspace,
		socket_root=tmp_path / "sockets",
	)
	broker.start()
	try:
		yield broker
	finally:
		broker.stop()


def test_the_socket_is_reachable_by_a_different_user(broker: BrokerProcess) -> None:
	"""The container's agent is uid 1000 and the host invoker generally is not.

	asyncio binds under the umask, so without an explicit chmod the socket lands
	at 0755 and every connect(2) fails with EACCES. The shim then fails open and
	the run finishes with an empty trace and no error anywhere, so this mode is
	asserted rather than assumed.
	"""
	socket_path = broker.socket_path
	assert socket_path is not None
	assert socket_path.stat().st_mode & 0o777 == 0o666
	# Traversable so the socket can be reached, but not listable.
	assert socket_path.parent.stat().st_mode & 0o777 == 0o711


def test_starting_fabricates_no_record(broker: BrokerProcess) -> None:
	# The broker writes a record for every connection it accepts, including one
	# that sends nothing, so nothing in startup may open a connection of its own.
	assert broker.command_count() == 0


def test_a_dead_broker_refuses_connections_instead_of_hanging(
	broker: BrokerProcess,
) -> None:
	"""A dead broker must refuse connections rather than accept and stall.

	While any live process holds the listening socket, a broker that has died
	still accepts into the backlog: connect(2) succeeds and the shim then blocks
	forever reading a decision that never comes, because it sets no receive
	timeout. A hung agent is far worse than a lost trace.

	This catches the supervisor holding the listener on the instance. It does
	not catch dropping the explicit close() alone, since the local goes out of
	scope and refcounting closes it anyway -- the close is there to make that
	deterministic rather than incidental.
	"""
	socket_path = broker.socket_path
	assert socket_path is not None

	# Reaching for the child directly is the point: nothing else can simulate a
	# broker that dies mid-run.
	process = broker._process
	assert process is not None
	process.kill()
	process.wait(timeout=10)

	assert socket_path.exists(), "the socket file outlives the broker"
	client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	client.settimeout(5.0)
	try:
		with pytest.raises(ConnectionRefusedError):
			client.connect(str(socket_path))
	finally:
		client.close()


def test_a_command_is_recorded_with_its_output(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	argv = ["/bin/bash", "-lc", "echo hello"]

	_run_one_command(socket_path, argv)

	assert _wait_for(lambda: broker.command_count() == 1)
	record = json.loads(broker.trace_path.read_text(encoding="utf-8").splitlines()[0])
	assert record["argv"] == argv
	assert record["exit_code"] == 0
	assert record["complete"] is True
	# The shape aebench audit-trace reads.
	assert record["monitors"]["timing"]["duration_ms"] >= 0
	captured = broker.trace_dir / record["stdout"]["path"]
	assert captured.read_bytes() == b"hello\n"


def test_concurrent_shells_each_get_a_record(broker: BrokerProcess) -> None:
	# A parallel build execs one shell per recipe line; a broker that served
	# them one at a time would deadlock the first `make -j`.
	socket_path = broker.socket_path
	assert socket_path is not None
	clients = []
	try:
		for index in range(20):
			client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
			client.connect(str(socket_path))
			info = {
				"argv": ["/bin/bash", "-c", f"job {index}"],
				"cwd": "/repo",
				"pid": os.getpid(),
				"env_keys": [],
			}
			client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))
			clients.append(client)

		# Every shell must get its verdict while all the others are still open.
		for client in clients:
			length, kind = _HEADER.unpack(client.recv(_HEADER.size))
			assert kind == _DECISION
			assert json.loads(client.recv(length))["allow"] is True

		for client in clients:
			client.sendall(
				_frame(_END, json.dumps({"exit_code": 0, "signal": None}).encode("utf-8"))
			)
	finally:
		for client in clients:
			client.close()

	assert _wait_for(lambda: broker.command_count() == len(clients))


def test_an_interrupted_shell_still_leaves_a_record(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	client.connect(str(socket_path))
	info = {
		"argv": ["/bin/bash", "-c", "sleep"],
		"cwd": "/repo",
		"pid": os.getpid(),
		"env_keys": [],
	}
	client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))
	length, _kind = _HEADER.unpack(client.recv(_HEADER.size))
	client.recv(length)
	# Killed before it could report an outcome, as an outer timeout would do.
	client.close()

	assert _wait_for(lambda: broker.command_count() == 1)
	record = json.loads(broker.trace_path.read_text(encoding="utf-8").splitlines()[0])
	assert record["complete"] is False
	assert record["exit_code"] is None


def test_stopping_releases_the_socket_and_keeps_the_trace(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=workspace,
		socket_root=tmp_path / "sockets",
	)
	broker.start()
	socket_dir = broker.socket_dir
	assert socket_dir is not None
	socket_path = broker.socket_path
	assert socket_path is not None
	_run_one_command(socket_path, ["/bin/bash", "-c", "true"])
	assert _wait_for(lambda: broker.command_count() == 1)

	broker.stop()
	broker.stop()  # idempotent: _cleanup may call it again

	assert not socket_dir.exists()
	assert broker.trace_path.is_file()
	assert broker.command_count() == 1
