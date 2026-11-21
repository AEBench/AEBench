"""What the shim reports about the process around the command.

These tests run the shim with stdio and parents of their own choosing, so they
start it directly rather than through ``run_shim``.
"""

from __future__ import annotations

import os
import pty
import subprocess
from pathlib import Path
from typing import IO, Any

import pytest

from .conftest import REAL_SHELL, _clean_path
from .fake_broker import FakeBroker

pytestmark = pytest.mark.shim


def _run(
	shim: Path,
	broker: FakeBroker,
	args: list[str],
	*,
	stdout: int | IO[Any] = subprocess.PIPE,
	stderr: int | IO[Any] = subprocess.PIPE,
) -> int:
	"""Runs the shim against ``broker`` and returns its exit status."""
	env = {
		"PATH": _clean_path(),
		"HOME": os.environ.get("HOME", "/tmp"),
		"AEBENCH_REAL_SHELL": REAL_SHELL,
		"AEBENCH_COMMAND_SOCKET": str(broker.socket_path),
	}
	process = subprocess.run(
		[str(shim), *args],
		stdin=subprocess.DEVNULL,
		stdout=stdout,
		stderr=stderr,
		env=env,
		timeout=60,
		check=False,
	)
	return process.returncode


def test_a_terminal_stdout_is_reported_as_a_tty(shim: Path, broker: FakeBroker) -> None:
	leader, follower = pty.openpty()
	try:
		assert _run(shim, broker, ["-c", "true"], stdout=follower) == 0
	finally:
		os.close(follower)
		os.close(leader)

	info = broker.wait_for_session().command_info
	assert info["stdout_tty"] is True
	assert info["stderr_tty"] is False


def test_piped_streams_are_not_reported_as_ttys(shim: Path, broker: FakeBroker) -> None:
	assert _run(shim, broker, ["-c", "true"]) == 0

	info = broker.wait_for_session().command_info
	assert info["stdout_tty"] is False
	assert info["stderr_tty"] is False
