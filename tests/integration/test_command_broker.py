"""End-to-end broker behaviour, driven by the reference shim client."""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from runtime.commands.broker import SOCKET_BASENAME, CommandBroker
from runtime.commands.interceptors import PatternDenyPolicy
from runtime.commands.runner import BaseInterceptor, CommandRunner
from runtime.commands.trace import CommandTraceWriter, read_command_trace

from .command_client import FakeShim


@pytest.fixture(name="output_dir")
def _output_dir(tmp_path: Path) -> Path:
	path = tmp_path / "output"
	path.mkdir()
	return path


@contextmanager
def _broker(
	output_dir: Path,
	*,
	interceptors: tuple[BaseInterceptor, ...] = (),
) -> Iterator[CommandBroker]:
	trace = CommandTraceWriter(output_dir)
	broker = CommandBroker(
		socket_path=output_dir / SOCKET_BASENAME,
		runner=CommandRunner(interceptors),
		trace=trace,
	)
	with broker:
		yield broker
	trace.close()


def _records(output_dir: Path) -> list[dict[str, Any]]:
	return read_command_trace(output_dir)


def test_a_monitored_command_is_recorded_with_its_streams(output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		with FakeShim(broker.socket_path) as shim:
			decision = shim.begin(("bash", "-lc", "make -j8"))
			assert decision.allow is True

			shim.write_stdout(b"building...\n")
			shim.write_stderr(b"warning: deprecated\n")
			shim.end(exit_code=0)

	[record] = _records(output_dir)
	assert record["command_id"] == "cmd_000001"
	assert record["shell_source"] == "make -j8"
	assert record["exit_code"] == 0
	assert record["duration_ms"] >= 0
	assert record["incomplete"] is False
	assert record["stdout_state"] == "captured"
	assert (output_dir / record["stdout_path"]).read_bytes() == b"building...\n"
	assert (output_dir / record["stderr_path"]).read_bytes() == b"warning: deprecated\n"


def test_a_denied_command_is_recorded_and_never_runs(output_dir: Path) -> None:
	policy = PatternDenyPolicy.from_strings((r"\bcurl\b",), reason="no network")
	with _broker(output_dir, interceptors=(policy,)) as broker:
		with FakeShim(broker.socket_path) as shim:
			decision = shim.begin(("bash", "-lc", "curl evil.example"))

	assert decision.allow is False
	assert decision.exit_code == 126
	assert "no network" in decision.reason

	[record] = _records(output_dir)
	assert record["allowed"] is False
	assert record["denied_by"] == "pattern_deny"
	assert record["exit_code"] == 126


def test_nesting_is_derived_from_the_real_process_tree(output_dir: Path) -> None:
	"""The broker walks /proc rather than trusting anything the shim says.

	The outer connection claims this test process; a genuine child of it must
	resolve back to that command even though the shim never mentions a parent.
	"""
	child = subprocess.Popen(["sleep", "30"])
	try:
		with _broker(output_dir) as broker:
			with FakeShim(broker.socket_path) as outer:
				outer.begin(("bash", "-lc", "make -j2"), pid=os.getpid())
				with FakeShim(broker.socket_path) as inner:
					inner.begin(("bash", "-c", "cc -c a.c"), pid=child.pid)
					inner.end(exit_code=0)
				outer.end(exit_code=0)
	finally:
		child.terminate()
		child.wait()

	records = {record["command_id"]: record for record in _records(output_dir)}
	assert records["cmd_000001"]["parent_command_id"] is None
	assert records["cmd_000002"]["parent_command_id"] == "cmd_000001"
	assert all(not record["incomplete"] for record in records.values())


def test_an_unrelated_process_has_no_parent(output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		with FakeShim(broker.socket_path) as first:
			first.begin(pid=os.getpid())
			first.end(exit_code=0)
		with FakeShim(broker.socket_path) as second:
			second.begin(pid=os.getpid())
			second.end(exit_code=0)

	# The first command is gone by the time the second connects, so nothing is
	# a live ancestor of it.
	assert all(record["parent_command_id"] is None for record in _records(output_dir))


def test_large_output_is_stored_in_full(output_dir: Path) -> None:
	payload = b"x" * (512 * 1024)
	with _broker(output_dir) as broker:
		with FakeShim(broker.socket_path) as shim:
			shim.begin()
			shim.write_stdout(payload)
			shim.end(exit_code=0)

	[record] = _records(output_dir)
	assert record["stdout_state"] == "captured"
	assert (output_dir / record["stdout_path"]).read_bytes() == payload


def test_a_silent_command_reports_nothing_captured(output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		with FakeShim(broker.socket_path) as shim:
			shim.begin()
			shim.end(exit_code=0)

	[record] = _records(output_dir)
	assert record["stdout_state"] == "not_captured"
	assert record["stderr_state"] == "not_captured"


def test_a_shim_that_dies_mid_command_still_leaves_a_record(output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		shim = FakeShim(broker.socket_path)
		shim.begin(("bash", "-lc", "sleep 100"))
		shim.write_stdout(b"partial")
		shim.close()
		_wait_for(lambda: len(_records(output_dir)) == 1)

	[record] = _records(output_dir)
	assert record["incomplete"] is True
	assert record["exit_code"] is None
	assert (output_dir / record["stdout_path"]).read_bytes() == b"partial"


def test_concurrent_commands_do_not_block_each_other(output_dir: Path) -> None:
	"""A long-running command must not stall the next one.

	This is the parallel-build case: one shim holds its connection open for the
	whole build while sibling shims connect, so a single-connection broker would
	deadlock here.
	"""
	with _broker(output_dir) as broker:
		with FakeShim(broker.socket_path) as slow, FakeShim(broker.socket_path) as quick:
			slow_decision = slow.begin(("bash", "-lc", "sleep 100"))
			quick_decision = quick.begin(("bash", "-lc", "echo hi"))

			quick.write_stdout(b"hi\n")
			quick.end(exit_code=0)
			slow.end(exit_code=0)

	assert slow_decision.command_id == "cmd_000001"
	assert quick_decision.command_id == "cmd_000002"
	assert {record["command_id"] for record in _records(output_dir)} == {
		"cmd_000001",
		"cmd_000002",
	}


def test_a_malformed_first_message_does_not_kill_the_broker(output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		rogue = FakeShim(broker.socket_path)
		rogue.send_raw(b"\x00\x00\x00\x04\x00junk")
		rogue.close()

		with FakeShim(broker.socket_path) as shim:
			decision = shim.begin()
			shim.end(exit_code=0)

	assert decision.allow is True
	assert [record["command_id"] for record in _records(output_dir)] == ["cmd_000001"]


def _wait_for(predicate: Any, *, timeout: float = 5.0) -> None:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if predicate():
			return
		time.sleep(0.01)
	raise AssertionError("condition was not met before the timeout")
