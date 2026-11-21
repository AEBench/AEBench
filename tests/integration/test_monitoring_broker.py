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
from datetime import datetime
from pathlib import Path

import pytest

from runtime.monitoring import BrokerProcess

pytestmark = pytest.mark.sanity

_HEADER = struct.Struct(">IB")
_COMMAND_INFO = 0
_STDOUT = 1
_END = 3
_DECISION = 4
_STARTED = 5


def _frame(kind: int, payload: bytes) -> bytes:
	return _HEADER.pack(len(payload), kind) + payload


def _wait_for(predicate: object, *, timeout: float = 10.0) -> bool:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if callable(predicate) and predicate():
			return True
		time.sleep(0.02)
	return False


def _run_one_command(
	socket_path: Path,
	argv: list[str],
	*,
	info: dict[str, object] | None = None,
	stdout: bytes = b"hello\n",
	end: dict[str, object] | None = None,
) -> dict[str, object]:
	"""Plays one command through the protocol the way the shim does.

	Returns:
		The decision the broker sent.
	"""
	client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	client.connect(str(socket_path))
	try:
		info = {
			"argv": argv,
			"cwd": "/repo",
			"pid": os.getpid(),
			"env_keys": ["PATH"],
			**(info or {}),
		}
		client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))

		header = client.recv(_HEADER.size)
		length, kind = _HEADER.unpack(header)
		decision = json.loads(client.recv(length))
		assert kind == _DECISION
		assert decision["allow"] is True

		if stdout:
			client.sendall(_frame(_STDOUT, stdout))
		end = {"exit_code": 0, "signal": None, **(end or {})}
		client.sendall(_frame(_END, json.dumps(end).encode("utf-8")))
	finally:
		client.close()
	return decision


def _records(broker: BrokerProcess) -> list[dict[str, object]]:
	"""Returns every record in the trace, in the order written."""
	lines = broker.trace_path.read_text(encoding="utf-8").splitlines()
	return [json.loads(line) for line in lines]


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


def test_a_tty_stream_is_recorded_as_skipped(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	_run_one_command(
		socket_path,
		["/bin/bash", "-c", "ls"],
		info={"stdout_tty": True, "stderr_tty": False},
		stdout=b"",
	)

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	assert record["stdout"]["capture"] == "skipped_tty"
	assert record["stderr"]["capture"] == "none"


def test_the_shim_version_is_recorded(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	_run_one_command(
		socket_path, ["/bin/bash", "-c", "ls"], info={"protocol": 2, "shim_version": "0.2.0"}
	)

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	assert record["protocol"] == 2
	assert record["shim_version"] == "0.2.0"


def test_every_record_carries_the_schema_version(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	_run_one_command(socket_path, ["/bin/bash", "-c", "ls"])
	_open_command(socket_path, {"argv": ["/bin/bash", "-c", "sleep"], "pid": 100}).close()

	assert _wait_for(lambda: broker.command_count() == 2)
	assert [record["schema_version"] for record in _records(broker)] == [2, 2]


def test_the_stdio_targets_are_recorded(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	stdio = ["/dev/null", "/repo/log", "pipe:[42]"]

	_run_one_command(socket_path, ["/bin/bash", "./run.sh"], info={"stdio": stdio})

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	assert record["stdio"] == stdio


def test_release_and_end_times_are_recorded(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	_run_one_command(socket_path, ["/bin/bash", "-c", "ls"], info={"t_info": 1700000000.5})

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	timing = record["monitors"]["timing"]
	released = datetime.fromisoformat(timing["released_at"])
	ended = datetime.fromisoformat(timing["ended_at"])
	assert released <= ended
	assert "released_at" not in record
	assert "ended_at" not in record
	assert record["shim_clock"]["info"] == 1700000000.5


def test_timing_runs_from_release_to_end(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	client = _open_command(socket_path, {"argv": ["/bin/bash", "-c", "sleep"], "pid": 100})
	time.sleep(0.2)
	client.sendall(_frame(_END, json.dumps({"exit_code": 0, "signal": None}).encode("utf-8")))
	client.close()

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	timing = record["monitors"]["timing"]
	released = datetime.fromisoformat(timing["released_at"])
	ended = datetime.fromisoformat(timing["ended_at"])
	assert timing["started_at"] == timing["released_at"]
	assert timing["duration_ms"] >= 200
	assert abs(timing["duration_ms"] - (ended - released).total_seconds() * 1000) <= 2
	assert isinstance(timing["snapshot_before_ms"], int) and timing["snapshot_before_ms"] >= 0
	assert isinstance(timing["snapshot_after_ms"], int) and timing["snapshot_after_ms"] >= 0


def test_a_record_without_an_end_has_no_end_time(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	_open_command(socket_path, {"argv": ["/bin/bash", "-c", "sleep"], "pid": 100}).close()

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	timing = record["monitors"]["timing"]
	assert timing["started_at"] == timing["released_at"]
	assert timing["ended_at"] is None
	assert timing["duration_ms"] is None
	assert timing["snapshot_after_ms"] is None


def _open_command(socket_path: Path, info: dict[str, object]) -> socket.socket:
	"""Starts a command and returns its connection, still open."""
	client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	client.connect(str(socket_path))
	info = {"cwd": "/repo", "env_keys": [], **info}
	client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))
	length, _kind = _HEADER.unpack(client.recv(_HEADER.size))
	client.recv(length)
	return client


def _record_for(broker: BrokerProcess, argv: list[str]) -> dict[str, object]:
	(record,) = [record for record in _records(broker) if record["argv"] == argv]
	return record


def test_a_nested_shell_is_linked_to_the_open_command_above_it(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	outer_argv = ["/bin/bash", "-c", "make"]
	inner_argv = ["/bin/bash", "-c", "cc -c a.c"]

	outer = _open_command(
		socket_path, {"argv": outer_argv, "pid": 100, "start_ticks": 5, "ancestry": []}
	)
	ancestry = [
		{"pid": 300, "start_ticks": 9, "comm": "make"},
		{"pid": 200, "start_ticks": 7, "comm": "bash"},
		{"pid": 100, "start_ticks": 5, "comm": "aeshell"},
	]
	_run_one_command(
		socket_path, inner_argv, info={"pid": 400, "start_ticks": 11, "ancestry": ancestry}
	)
	outer.sendall(_frame(_END, json.dumps({"exit_code": 0, "signal": None}).encode("utf-8")))
	outer.close()

	assert _wait_for(lambda: broker.command_count() == 2)
	parent = _record_for(broker, outer_argv)
	child = _record_for(broker, inner_argv)
	assert parent["parent_command_id"] is None
	assert parent["parent_link"] == "none"
	assert parent["depth"] == 0
	assert child["parent_command_id"] == parent["command_id"]
	assert child["root_command_id"] == parent["command_id"]
	assert child["parent_link"] == "live"
	assert child["depth"] == 1


def test_a_reused_pid_is_not_taken_for_a_finished_parent(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	first_argv = ["/bin/bash", "-c", "first"]
	later_argv = ["/bin/bash", "-c", "later"]

	_run_one_command(socket_path, first_argv, info={"pid": 100, "start_ticks": 5, "ancestry": []})
	_run_one_command(
		socket_path,
		later_argv,
		info={
			"pid": 400,
			"start_ticks": 20,
			"ancestry": [{"pid": 100, "start_ticks": 19, "comm": "x"}],
		},
	)

	assert _wait_for(lambda: broker.command_count() == 2)
	assert _record_for(broker, later_argv)["parent_command_id"] is None


def test_a_shell_started_after_its_parent_finished_is_linked_as_closed(
	broker: BrokerProcess,
) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	first_argv = ["/bin/bash", "-c", "first"]
	later_argv = ["/bin/bash", "-c", "later"]

	_run_one_command(socket_path, first_argv, info={"pid": 100, "start_ticks": 5, "ancestry": []})
	assert _wait_for(lambda: broker.command_count() == 1)
	_run_one_command(
		socket_path,
		later_argv,
		info={
			"pid": 400,
			"start_ticks": 20,
			"ancestry": [{"pid": 100, "start_ticks": 5, "comm": "aeshell"}],
		},
	)

	assert _wait_for(lambda: broker.command_count() == 2)
	later = _record_for(broker, later_argv)
	assert later["parent_command_id"] == _record_for(broker, first_argv)["command_id"]
	assert later["parent_link"] == "closed"


def test_a_shell_under_a_killed_shim_links_through_the_real_shell(
	broker: BrokerProcess,
) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	outer_argv = ["/bin/bash", "-c", "./experiment.sh"]
	inner_argv = ["/bin/bash", "-c", "python3 eval.py"]

	outer = _open_command(
		socket_path, {"argv": outer_argv, "pid": 100, "start_ticks": 5, "ancestry": []}
	)
	started = {"child_pid": 200, "child_start_ticks": 7, "t_spawn": 1700000000.0}
	outer.sendall(_frame(_STARTED, json.dumps(started).encode("utf-8")))
	# Killed before it could send `end`, as `kill -9` would do.
	outer.close()
	assert _wait_for(lambda: broker.command_count() == 1)

	_run_one_command(
		socket_path,
		inner_argv,
		info={
			"pid": 400,
			"start_ticks": 9,
			"ancestry": [{"pid": 200, "start_ticks": 7, "comm": "bash"}],
		},
	)

	assert _wait_for(lambda: broker.command_count() == 2)
	parent = _record_for(broker, outer_argv)
	assert parent["child_pid"] == 200
	assert parent["shim_clock"]["spawn"] == 1700000000.0
	child = _record_for(broker, inner_argv)
	assert child["parent_command_id"] == parent["command_id"]
	assert child["parent_link"] == "closed"


def test_a_shell_started_by_an_orphan_is_linked_as_adopted(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	launch_argv = ["/bin/bash", "-c", "nohup ./run.sh > log 2>&1 &"]
	later_argv = ["/bin/bash", "-c", "python3 eval.py"]
	orphans = [{"pid": 500, "start_ticks": 3, "comm": "run.sh"}]

	decision = _run_one_command(
		socket_path,
		launch_argv,
		info={"pid": 100, "start_ticks": 1, "ancestry": []},
		end={"orphans": orphans},
	)
	assert decision["subreaper"] is True
	assert _wait_for(lambda: broker.command_count() == 1)
	_run_one_command(
		socket_path,
		later_argv,
		info={
			"pid": 600,
			"start_ticks": 8,
			"ancestry": [{"pid": 500, "start_ticks": 3, "comm": "run.sh"}],
		},
	)

	assert _wait_for(lambda: broker.command_count() == 2)
	launch = _record_for(broker, launch_argv)
	assert launch["orphans"] == orphans
	later = _record_for(broker, later_argv)
	assert later["parent_command_id"] == launch["command_id"]
	assert later["parent_link"] == "adopted"


def test_an_orphaned_shim_keeps_its_own_command(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	launch_argv = ["/bin/bash", "-c", "nohup bash -c ./run.sh &"]
	detached_argv = ["/bin/bash", "-c", "./run.sh"]
	inner_argv = ["/bin/bash", "-c", "python3 eval.py"]

	launch = _open_command(
		socket_path, {"argv": launch_argv, "pid": 100, "start_ticks": 1, "ancestry": []}
	)
	_run_one_command(
		socket_path,
		detached_argv,
		info={
			"pid": 500,
			"start_ticks": 3,
			"ancestry": [{"pid": 100, "start_ticks": 1, "comm": "aeshell"}],
		},
	)
	orphans = [{"pid": 500, "start_ticks": 3, "comm": "aeshell"}]
	end = {"exit_code": 0, "signal": None, "orphans": orphans}
	launch.sendall(_frame(_END, json.dumps(end).encode("utf-8")))
	launch.close()
	assert _wait_for(lambda: broker.command_count() == 2)
	_run_one_command(
		socket_path,
		inner_argv,
		info={
			"pid": 600,
			"start_ticks": 8,
			"ancestry": [{"pid": 500, "start_ticks": 3, "comm": "aeshell"}],
		},
	)

	assert _wait_for(lambda: broker.command_count() == 3)
	inner = _record_for(broker, inner_argv)
	assert inner["parent_command_id"] == _record_for(broker, detached_argv)["command_id"]
	assert inner["depth"] == 2


def test_an_orphan_listed_by_two_commands_is_marked_inherited(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None
	verify_argv = ["/bin/bash", "-c", "./verify.sh"]
	dafny_argv = ["/bin/bash", "-c", "dafny verify a.dfy"]
	z3 = {"pid": 700, "start_ticks": 12, "comm": "z3"}

	verify = _open_command(
		socket_path, {"argv": verify_argv, "pid": 100, "start_ticks": 1, "ancestry": []}
	)
	_run_one_command(
		socket_path,
		dafny_argv,
		info={
			"pid": 300,
			"start_ticks": 4,
			"ancestry": [{"pid": 100, "start_ticks": 1, "comm": "aeshell"}],
		},
		end={"orphans": [z3]},
	)
	assert _wait_for(lambda: broker.command_count() == 1)
	end = {"exit_code": 0, "signal": None, "orphans": [z3]}
	verify.sendall(_frame(_END, json.dumps(end).encode("utf-8")))
	verify.close()

	assert _wait_for(lambda: broker.command_count() == 2)
	dafny = _record_for(broker, dafny_argv)
	assert dafny["orphans"] == [z3]
	(inherited,) = _record_for(broker, verify_argv)["orphans"]
	assert inherited == {**z3, "inherited_from": dafny["command_id"]}


def test_a_record_without_ancestry_leaves_its_links_unknown(broker: BrokerProcess) -> None:
	socket_path = broker.socket_path
	assert socket_path is not None

	_run_one_command(socket_path, ["/bin/bash", "-c", "ls"])

	assert _wait_for(lambda: broker.command_count() == 1)
	(record,) = _records(broker)
	assert record["parent_link"] is None
	assert record["root_command_id"] is None


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


def test_a_command_that_edits_a_tracked_file_is_attributed_the_change(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	(workspace / "run.sh").write_text("echo run\n", encoding="utf-8")
	(workspace / "plot.py").write_text("print(1)\n", encoding="utf-8")
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=workspace,
		socket_root=tmp_path / "sockets",
	)
	broker.start()
	try:
		socket_path = broker.socket_path
		assert socket_path is not None
		client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		client.connect(str(socket_path))
		try:
			info = {"argv": ["/bin/bash", "-c", "edit"], "cwd": "/repo", "pid": 1, "env_keys": []}
			client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))
			length, _kind = _HEADER.unpack(client.recv(_HEADER.size))
			client.recv(length)

			# The "command" runs between the verdict and END.
			(workspace / "run.sh").write_text("echo faked results\n", encoding="utf-8")
			(workspace / "plot.py").unlink()
			(workspace / "build.log").write_text("untracked\n", encoding="utf-8")

			client.sendall(
				_frame(_END, json.dumps({"exit_code": 0, "signal": None}).encode("utf-8"))
			)
		finally:
			client.close()

		assert _wait_for(lambda: broker.command_count() == 1)
		record = json.loads(broker.trace_path.read_text(encoding="utf-8").splitlines()[0])
		snapshot = record["monitors"]["file_snapshot"]
		assert snapshot["scope"] == "tracked"
		assert snapshot["modified"] == ["run.sh"]
		assert snapshot["deleted"] == ["plot.py"]
	finally:
		broker.stop()


def test_a_same_size_rewrite_with_a_restored_mtime_is_still_attributed(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	script = workspace / "aggregate.py"
	script.write_text("print('measured')\n", encoding="utf-8")
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=workspace,
		socket_root=tmp_path / "sockets",
	)
	broker.start()
	try:
		socket_path = broker.socket_path
		assert socket_path is not None
		client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		client.connect(str(socket_path))
		try:
			info = {"argv": ["/bin/bash", "-c", "hide"], "cwd": "/repo", "pid": 1, "env_keys": []}
			client.sendall(_frame(_COMMAND_INFO, json.dumps(info).encode("utf-8")))
			length, _kind = _HEADER.unpack(client.recv(_HEADER.size))
			client.recv(length)

			# Size and mtime end up exactly as before, so only ctime chagnes.
			before = script.stat()
			script.write_text("print('injected')\n", encoding="utf-8")
			os.utime(script, ns=(before.st_atime_ns, before.st_mtime_ns))
			assert script.stat().st_size == before.st_size

			client.sendall(
				_frame(_END, json.dumps({"exit_code": 0, "signal": None}).encode("utf-8"))
			)
		finally:
			client.close()

		assert _wait_for(lambda: broker.command_count() == 1)
		record = json.loads(broker.trace_path.read_text(encoding="utf-8").splitlines()[0])
		assert record["monitors"]["file_snapshot"]["modified"] == ["aggregate.py"]
	finally:
		broker.stop()


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
