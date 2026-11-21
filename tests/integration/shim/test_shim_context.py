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
import tomllib

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


def test_stdio_targets_are_reported(shim: Path, broker: FakeBroker, tmp_path: Path) -> None:
	log = tmp_path / "out.log"
	with log.open("w") as handle:
		assert _run(shim, broker, ["-c", "true"], stdout=handle) == 0

	stdin, stdout, stderr = broker.wait_for_session().command_info["stdio"]
	assert stdin == "/dev/null"
	assert stdout == str(log)
	assert stderr.startswith("pipe:[")


def test_the_protocol_and_shim_version_are_reported(shim: Path, broker: FakeBroker) -> None:
	manifest = tomllib.loads((shim.parents[2] / "Cargo.toml").read_text(encoding="utf-8"))

	assert _run(shim, broker, ["-c", "true"]) == 0

	info = broker.wait_for_session().command_info
	assert info["protocol"] == 2
	assert info["shim_version"] == manifest["package"]["version"]
