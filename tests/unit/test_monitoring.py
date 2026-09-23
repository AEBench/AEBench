"""Shim installation and broker supervision, without Docker or the Rust binary."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Mapping

import pytest

from runtime import monitoring
from runtime.monitoring import (
	MAX_SOCKET_PATH_BYTES,
	PROBE_SENTINEL,
	REAL_SHELL_PATH,
	SHIM_IMAGE_PATH,
	BrokerProcess,
	install_shim,
	verify_monitoring,
)


class FakeRuntime:
	"""Records what the case runner asked the container to do."""

	path_separator = ":"

	def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
		self.commands: list[list[str]] = []
		self.returncode = returncode
		self.stdout = stdout
		self.stderr = stderr
		self.on_run: object | None = None

	def run_process(
		self,
		cmd: list[str],
		*,
		cwd: str | None = None,
		env: Mapping[str, str] | None = None,
		stdin_text: str | None = None,
		timeout: float = 5.0,
	) -> subprocess.CompletedProcess[str]:
		_ = cwd, env, stdin_text, timeout
		self.commands.append(cmd)
		if callable(self.on_run):
			self.on_run()
		return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def test_install_shim_preserves_the_real_shell_and_renames_into_place() -> None:
	runtime = FakeRuntime()
	install_shim(runtime)  # type: ignore[arg-type]

	(command,) = runtime.commands
	assert command[:3] == ["sh", "-e", "-c"]
	script = command[3]
	assert SHIM_IMAGE_PATH in script
	assert REAL_SHELL_PATH in script
	# rename(2) rather than writing onto the live binary, which fails ETXTBSY.
	assert 'mv -f "$target.aebench-new" "$target"' in script
	assert 'cp -p "$shim" "$target"' not in script
	# The real shell is captured once, so a re-install cannot overwrite it with
	# the shim and leave nothing to fall back to.
	assert 'if [ ! -e "$real" ]; then cp -p "$target" "$real"; fi' in script


def test_install_shim_reports_a_missing_shim_rather_than_continuing() -> None:
	runtime = FakeRuntime(returncode=2, stderr="aeshell is missing from the image")

	with pytest.raises(RuntimeError) as excinfo:
		install_shim(runtime)  # type: ignore[arg-type]

	assert "install the command shim" in str(excinfo.value)
	# The script's own message reaches the caller, which is what makes the
	# failure diagnosable without a dedicated exception type.
	assert "aeshell is missing from the image" in str(excinfo.value)


def test_socket_path_beyond_sun_path_is_rejected_before_binding(tmp_path: Path) -> None:
	# AF_UNIX truncates rather than failing, so a long path must be caught here.
	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=tmp_path,
		socket_root=tmp_path / ("d" * 120),
	)

	with pytest.raises(RuntimeError) as excinfo:
		broker.start()

	assert f"AF_UNIX allows {MAX_SOCKET_PATH_BYTES}" in str(excinfo.value)
	broker.stop()


def test_verify_probes_as_the_agent_user(tmp_path: Path) -> None:
	# Running the probe as root would pass against a socket the agent cannot
	# open, which is exactly the failure the probe exists to catch.
	broker = BrokerProcess(trace_dir=tmp_path, workspace_dir=tmp_path, socket_root=tmp_path)
	runtime = FakeRuntime(stdout=f"{PROBE_SENTINEL}\n")
	runtime.on_run = lambda: broker.trace_path.write_text('{"command_id": "1"}\n', encoding="utf-8")

	verify_monitoring(runtime, broker)  # type: ignore[arg-type]

	(command,) = runtime.commands
	assert command == [
		"runuser",
		"--user",
		"agent",
		"--",
		"/bin/bash",
		"-c",
		f"echo {PROBE_SENTINEL}",
	]


def test_verify_fails_when_the_shim_never_reaches_the_broker(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setattr(monitoring, "_PROBE_RECORD_TIMEOUT_SECONDS", 0.2)
	broker = BrokerProcess(trace_dir=tmp_path, workspace_dir=tmp_path, socket_root=tmp_path)
	# A working shell, but nothing arrives: a wrong mount or unreachable socket.
	runtime = FakeRuntime(stdout=f"{PROBE_SENTINEL}\n")

	with pytest.raises(RuntimeError) as excinfo:
		verify_monitoring(runtime, broker)  # type: ignore[arg-type]

	assert "never reached the command broker" in str(excinfo.value)


def test_verify_fails_when_the_swapped_shell_is_broken(tmp_path: Path) -> None:
	broker = BrokerProcess(trace_dir=tmp_path, workspace_dir=tmp_path, socket_root=tmp_path)
	runtime = FakeRuntime(returncode=127, stderr="bash: cannot execute")

	with pytest.raises(RuntimeError) as excinfo:
		verify_monitoring(runtime, broker)  # type: ignore[arg-type]

	assert "did not behave like a shell" in str(excinfo.value)


def test_command_count_is_zero_before_the_broker_writes_anything(tmp_path: Path) -> None:
	broker = BrokerProcess(trace_dir=tmp_path, workspace_dir=tmp_path, socket_root=tmp_path)
	assert broker.command_count() == 0
