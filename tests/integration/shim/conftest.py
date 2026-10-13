"""Fixtures for the aeshell integration tests.

The binary under test is built once per session. Every test drives it as a real
subprocess, exactly as an agent would, so nothing here reaches into the shim's
internals.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

import pytest

from .fake_broker import FakeBroker

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIM_DIR = _REPO_ROOT / "src" / "runtime" / "shim"
_BINARY = _SHIM_DIR / "target" / "release" / "aeshell"

#: The shell the shim execs. Recorded so a test can tell it apart from argv[0].
REAL_SHELL = "/bin/bash"

#: Sentinel meaning "point the shim at the broker this test is using".
_BROKER_SOCKET: Any = object()


def _clean_path() -> str:
	"""Returns PATH without the .fakebin directory the integration suite injects.

	That directory contains a stub ``cargo`` which prints a version and builds
	nothing, so a build resolved through it would silently produce no binary.
	"""
	entries = [
		entry
		for entry in os.environ.get("PATH", "").split(os.pathsep)
		if entry and not entry.endswith(".fakebin")
	]
	return os.pathsep.join(entries)


class ShimRunner(Protocol):
	"""Runs the shim once and returns the completed process."""

	def __call__(
		self,
		args: Sequence[str],
		*,
		input: str | None = None,
		cwd: Path | None = None,
		env: Mapping[str, str] | None = None,
		socket_path: Any = _BROKER_SOCKET,
		timeout: float = 60.0,
	) -> subprocess.CompletedProcess[str]:
		"""Runs ``aeshell`` with ``args``."""
		...


@pytest.fixture(scope="session", name="shim")
def _shim() -> Path:
	"""Builds the shim once per session and returns the binary."""
	path = _clean_path()
	cargo = shutil.which("cargo", path=path)
	if cargo is None:
		pytest.skip("cargo is not installed")
	if not Path(REAL_SHELL).is_file():
		pytest.skip(f"{REAL_SHELL} is not present")

	build = subprocess.run(
		[cargo, "build", "--release"],
		cwd=_SHIM_DIR,
		capture_output=True,
		text=True,
		env={**os.environ, "PATH": path},
		check=False,
	)
	if build.returncode != 0 or not _BINARY.is_file():
		pytest.fail(f"cargo build failed:\n{(build.stderr or build.stdout).strip()}")
	return _BINARY


@pytest.fixture(name="broker")
def _broker(tmp_path: Path) -> Iterator[FakeBroker]:
	"""Starts a fake broker on a per-test socket."""
	broker = FakeBroker(tmp_path / "command.sock")
	with broker.start():
		yield broker


@pytest.fixture(name="run_shim")
def _run_shim(shim: Path, broker: FakeBroker) -> ShimRunner:
	"""Returns a helper that runs the shim against the test's broker.

	The environment is built from scratch rather than inherited, so a test only
	ever sees variables it asked for. Pass ``socket_path=None`` to leave
	``AEBENCH_COMMAND_SOCKET`` unset, or a path of your own to point the shim
	somewhere else.
	"""

	def run(
		args: Sequence[str],
		*,
		input: str | None = None,
		cwd: Path | None = None,
		env: Mapping[str, str] | None = None,
		socket_path: Any = _BROKER_SOCKET,
		timeout: float = 60.0,
	) -> subprocess.CompletedProcess[str]:
		child_env = {
			"PATH": _clean_path(),
			"HOME": os.environ.get("HOME", "/tmp"),
			"AEBENCH_REAL_SHELL": REAL_SHELL,
			**(env or {}),
		}
		if socket_path is _BROKER_SOCKET:
			child_env["AEBENCH_COMMAND_SOCKET"] = str(broker.socket_path)
		elif socket_path is not None:
			child_env["AEBENCH_COMMAND_SOCKET"] = str(socket_path)

		return subprocess.run(
			[str(shim), *args],
			input=input,
			capture_output=True,
			text=True,
			env=child_env,
			cwd=None if cwd is None else str(cwd),
			timeout=timeout,
			check=False,
		)

	return run
