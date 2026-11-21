"""What the shim reports about the process around the command.

These tests run the shim with stdio and parents of their own choosing, so they
start it directly rather than through ``run_shim``.
"""

from __future__ import annotations

import os
import pty
import signal
import subprocess
import time
from pathlib import Path
from typing import IO, Any

import pytest
import tomllib

from .conftest import REAL_SHELL, _clean_path
from .fake_broker import Decision, FakeBroker

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


def test_the_shim_clock_brackets_the_command(shim: Path, broker: FakeBroker) -> None:
	before = time.time()
	assert _run(shim, broker, ["-c", "sleep 0.2"]) == 0
	after = time.time()

	session = broker.wait_for_session()
	assert session.end is not None
	assert before <= session.command_info["t_info"] <= session.end["t_exit"] <= after
	assert session.end["t_exit"] - session.command_info["t_info"] >= 0.2


def test_a_nested_shell_reports_the_outer_shim_among_its_ancestors(
	shim: Path, broker: FakeBroker
) -> None:
	# The trailing `true` stops bash from exec'ing the inner shim in place.
	assert _run(shim, broker, ["-c", f"{shim} -c true; true"]) == 0

	outer = broker.wait_for_session(0).command_info
	inner = broker.wait_for_session(1).command_info
	ancestry = inner["ancestry"]
	assert inner["ppid"] == ancestry[0]["pid"]
	assert ancestry[0]["comm"] == "bash"
	assert {"pid": outer["pid"], "start_ticks": outer["start_ticks"]} in [
		{"pid": entry["pid"], "start_ticks": entry["start_ticks"]} for entry in ancestry
	]
	assert isinstance(inner["pgid"], int) and isinstance(inner["sid"], int)


def test_the_started_frame_names_the_real_shell_a_nested_shell_runs_under(
	shim: Path, broker: FakeBroker
) -> None:
	before = time.time()
	assert _run(shim, broker, ["-c", f"{shim} -c true; true"]) == 0

	outer = broker.wait_for_session(0)
	inner = broker.wait_for_session(1).command_info
	assert outer.started is not None
	assert outer.started["child_pid"] == inner["ppid"]
	assert outer.started["child_start_ticks"] == inner["ancestry"][0]["start_ticks"]
	assert before <= outer.started["t_spawn"] <= inner["t_info"]


def test_a_subreaper_shim_reports_what_its_command_left_running(shim: Path, tmp_path: Path) -> None:
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		started = time.monotonic()
		assert _run(shim, broker, ["-c", "nohup sleep 3 >/dev/null 2>&1 &"]) == 0
		assert time.monotonic() - started < 3

		end = broker.wait_for_session().end
	assert end is not None
	(orphan,) = end["orphans"]
	# Listed at once, so the forked job may not have exec'd sleep yet.
	assert orphan["comm"] in {"bash", "nohup", "sleep"}
	assert orphan["start_ticks"] > 0


def test_an_orphan_that_exits_at_once_is_not_reported(shim: Path, tmp_path: Path) -> None:
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		assert _run(shim, broker, ["-c", "sleep 0.03 >/dev/null 2>&1 &"]) == 0

		end = broker.wait_for_session().end
	assert end is not None
	assert end["orphans"] == []


def test_a_subreaper_shim_reaps_orphans_that_exit(shim: Path, tmp_path: Path) -> None:
	script = (
		"pid=$(sleep 0.1 >/dev/null 2>&1 & echo $!); sleep 0.6; "
		"if kill -0 $pid 2>/dev/null; then echo zombie; else echo reaped; fi"
	)
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		assert _run(shim, broker, ["-c", script]) == 0

		session = broker.wait_for_session()
	assert session.stdout == b"reaped\n"


def test_a_subreaper_shim_reaps_orphans_while_output_drains(shim: Path, tmp_path: Path) -> None:
	# The subshell holds stdout open after the command exits.
	script = (
		"pid=$(sleep 0.3 >/dev/null 2>&1 & echo $!); "
		'(sleep 0.8; cut -d" " -f3 /proc/$pid/stat 2>/dev/null || echo gone) & exit 0'
	)
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		assert _run(shim, broker, ["-c", script]) == 0

		session = broker.wait_for_session()
	assert session.stdout == b"gone\n"


@pytest.mark.parametrize(
	("script", "status", "exit_code", "signal"),
	[("exit 7", 7, 7, None), ("kill -TERM $$", 128 + 15, None, 15)],
)
def test_a_subreaper_shim_reproduces_the_exit_status(
	shim: Path, tmp_path: Path, script: str, status: int, exit_code: int | None, signal: int | None
) -> None:
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		assert _run(shim, broker, ["-c", f"(sleep 0.1 >/dev/null 2>&1 &); {script}"]) == status

		end = broker.wait_for_session().end
	assert end is not None
	assert (end["exit_code"], end["signal"]) == (exit_code, signal)


def test_a_subreaper_shim_forwards_a_signal_to_its_command(shim: Path, tmp_path: Path) -> None:
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		env = {
			"PATH": _clean_path(),
			"AEBENCH_REAL_SHELL": REAL_SHELL,
			"AEBENCH_COMMAND_SOCKET": str(broker.socket_path),
		}
		process = subprocess.Popen(
			[str(shim), "-c", "sleep 5 >/dev/null 2>&1; true"],
			stdin=subprocess.DEVNULL,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			env=env,
		)
		deadline = time.monotonic() + 10
		while not broker.sessions or broker.sessions[0].started is None:
			assert time.monotonic() < deadline
			time.sleep(0.02)
		process.send_signal(signal.SIGTERM)

		assert process.wait(timeout=10) == 128 + 15
		end = broker.wait_for_session().end
	assert end is not None
	assert end["signal"] == 15


def _ignore_sigchld() -> None:
	signal.signal(signal.SIGCHLD, signal.SIG_IGN)


def test_an_ignored_sigchld_neither_hangs_nor_hides_the_exit_status(
	shim: Path, tmp_path: Path
) -> None:
	script = "grep SigIgn /proc/self/status; sleep 1.5 >/dev/null 2>&1 & exit 5"
	with FakeBroker(tmp_path / "reaper.sock", Decision(subreaper=True)).start() as broker:
		env = {
			"PATH": _clean_path(),
			"AEBENCH_REAL_SHELL": REAL_SHELL,
			"AEBENCH_COMMAND_SOCKET": str(broker.socket_path),
		}
		started = time.monotonic()
		process = subprocess.run(
			[str(shim), "-c", script],
			stdin=subprocess.DEVNULL,
			capture_output=True,
			env=env,
			preexec_fn=_ignore_sigchld,
			timeout=60,
			check=False,
		)
		assert time.monotonic() - started < 1.2
		end = broker.wait_for_session().end

	assert process.returncode == 5
	assert end is not None
	assert end["exit_code"] == 5
	# The command still inherits the ignored SIGCHLD.
	ignored = int(process.stdout.split()[1], 16)
	assert ignored & 1 << (signal.SIGCHLD - 1)


def test_without_a_subreaper_orphans_are_unknown(shim: Path, broker: FakeBroker) -> None:
	assert _run(shim, broker, ["-c", "nohup sleep 1 >/dev/null 2>&1 &"]) == 0

	end = broker.wait_for_session().end
	assert end is not None
	assert end["orphans"] is None


def test_the_protocol_and_shim_version_are_reported(shim: Path, broker: FakeBroker) -> None:
	manifest = tomllib.loads((shim.parents[2] / "Cargo.toml").read_text(encoding="utf-8"))

	assert _run(shim, broker, ["-c", "true"]) == 0

	info = broker.wait_for_session().command_info
	assert info["protocol"] == 2
	assert info["shim_version"] == manifest["package"]["version"]
