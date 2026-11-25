"""Shim installation and broker supervision, without Docker or the Rust binary."""

from __future__ import annotations

import signal
import subprocess
import tempfile
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
	# A failed start cleans up after itself; the caller need not call stop().
	assert list((tmp_path / ("d" * 120)).iterdir()) == []
	assert broker.socket_dir is None


def test_a_start_that_fails_to_spawn_leaves_nothing_behind(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	# Fails late: the socket is already bound and chmodded when the spawn fails.
	def refuse(*_args: object, **_kwargs: object) -> None:
		raise OSError("fork failed")

	monkeypatch.setattr(monitoring.subprocess, "Popen", refuse)
	with tempfile.TemporaryDirectory(prefix="aebench-test-", dir="/tmp") as socket_root:
		broker = BrokerProcess(
			trace_dir=tmp_path / "trace",
			workspace_dir=tmp_path,
			socket_root=Path(socket_root),
		)

		with pytest.raises(OSError, match="fork failed"):
			broker.start()

		assert list(Path(socket_root).iterdir()) == []
		assert broker.socket_dir is None


class FakeProcess:
	def __init__(self, *, status: int = 0, hangs: bool = False) -> None:
		self.status = status
		self.hangs = hangs
		self.signals: list[int] = []
		self.killed = False
		self.waited = False

	def send_signal(self, signum: int) -> None:
		self.signals.append(signum)

	def kill(self) -> None:
		self.killed = True

	def wait(self, timeout: float | None = None) -> int:
		if self.hangs and not self.killed:
			raise subprocess.TimeoutExpired("monitor.py", timeout or 0)
		self.waited = True
		return -9 if self.killed else self.status


def _stopped(tmp_path: Path, process: FakeProcess) -> BrokerProcess:
	broker = BrokerProcess(trace_dir=tmp_path, workspace_dir=tmp_path, socket_root=tmp_path)
	broker._process = process  # type: ignore[assignment]
	broker.stop()
	return broker


def test_stop_terminates_the_broker_and_reaps_it(tmp_path: Path) -> None:
	# SIGTERM, not SIGKILL: a snapshotting broker takes its final snapshot on the way out.
	process = FakeProcess()
	broker = _stopped(tmp_path, process)

	assert process.signals == [signal.SIGTERM]
	assert process.waited is True
	assert process.killed is False
	assert broker.stop_error is None


def test_stop_kills_a_broker_that_ignores_sigterm(tmp_path: Path) -> None:
	process = FakeProcess(hangs=True)
	broker = _stopped(tmp_path, process)

	assert process.killed is True
	assert process.waited is True
	assert broker.stop_error is not None and "did not exit" in broker.stop_error


def test_stop_reports_a_failed_final_snapshot(tmp_path: Path) -> None:
	broker = _stopped(tmp_path, FakeProcess(status=3))

	assert broker.stop_error is not None and "final workdir snapshot failed" in broker.stop_error


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
