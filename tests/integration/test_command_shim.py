"""The real Rust shim driven against the real broker.

These tests are the only place the shim's transparency claims are checked:
argv passthrough, stdout/stderr fidelity, exit status (including signals), and
fail-open behaviour when no broker is listening.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from runtime.commands.broker import SOCKET_BASENAME, CommandBroker
from runtime.commands.interceptors import PatternDenyPolicy
from runtime.commands.runner import BaseInterceptor, CommandRunner
from runtime.commands.trace import CommandTraceWriter, read_command_trace

_SHIM_DIR = Path(__file__).resolve().parents[2] / "src" / "runtime" / "shim"
_BINARY = _SHIM_DIR / "target" / "release" / "aeshell"


def _clean_path() -> str:
	"""Returns PATH without the fake-binary directory this suite injects."""
	entries = [
		entry
		for entry in os.environ.get("PATH", "").split(os.pathsep)
		if entry and not entry.endswith(".fakebin")
	]
	return os.pathsep.join(entries)


@pytest.fixture(scope="session", name="shim")
def _shim() -> Path:
	path = _clean_path()
	cargo = shutil.which("cargo", path=path)
	if cargo is None:
		pytest.skip("cargo is not installed")

	build = subprocess.run(
		[cargo, "build", "--release"],
		cwd=_SHIM_DIR,
		capture_output=True,
		text=True,
		env={**os.environ, "PATH": path},
		check=False,
	)
	if build.returncode != 0 or not _BINARY.is_file():
		pytest.skip(f"cargo build failed: {(build.stderr or build.stdout).strip()[:400]}")
	return _BINARY


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


def _run(
	shim: Path,
	args: list[str],
	*,
	socket_path: Path | None,
	cwd: Path | None = None,
	extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
	env = {
		"PATH": _clean_path(),
		"HOME": os.environ.get("HOME", "/tmp"),
		"AEBENCH_REAL_SHELL": "/bin/bash",
		**(extra_env or {}),
	}
	if socket_path is not None:
		env["AEBENCH_COMMAND_SOCKET"] = str(socket_path)
	return subprocess.run(
		[str(shim), *args],
		capture_output=True,
		text=True,
		env=env,
		cwd=None if cwd is None else str(cwd),
		timeout=60,
		check=False,
	)


def _records(output_dir: Path) -> list[dict[str, Any]]:
	return read_command_trace(output_dir)


@pytest.fixture(name="output_dir")
def _output_dir(tmp_path: Path) -> Path:
	path = tmp_path / "output"
	path.mkdir()
	return path


def test_output_and_exit_code_pass_through_untouched(shim: Path, output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		result = _run(
			shim,
			["-c", "echo to-stdout; echo to-stderr >&2; exit 7"],
			socket_path=broker.socket_path,
		)

	assert result.returncode == 7
	assert result.stdout == "to-stdout\n"
	assert result.stderr == "to-stderr\n"

	[record] = _records(output_dir)
	assert record["exit_code"] == 7
	assert record["signal"] is None
	assert record["shell_source"] == "echo to-stdout; echo to-stderr >&2; exit 7"
	assert record["incomplete"] is False
	assert (output_dir / record["stdout_path"]).read_text() == "to-stdout\n"
	assert (output_dir / record["stderr_path"]).read_text() == "to-stderr\n"


def test_a_signalled_command_reports_the_signal_and_exits_128_plus(
	shim: Path, output_dir: Path
) -> None:
	with _broker(output_dir) as broker:
		result = _run(shim, ["-c", "kill -TERM $$"], socket_path=broker.socket_path)

	assert result.returncode == 128 + 15

	[record] = _records(output_dir)
	assert record["signal"] == 15
	assert record["exit_code"] is None


def test_the_working_directory_is_reported(shim: Path, output_dir: Path, tmp_path: Path) -> None:
	workdir = tmp_path / "workspace"
	workdir.mkdir()
	with _broker(output_dir) as broker:
		_run(shim, ["-c", "pwd"], socket_path=broker.socket_path, cwd=workdir)

	[record] = _records(output_dir)
	assert record["cwd"] == str(workdir)


def test_stdin_reaches_the_command(shim: Path, output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		result = subprocess.run(
			[str(shim), "-c", "cat"],
			input="piped-in\n",
			capture_output=True,
			text=True,
			env={
				"PATH": _clean_path(),
				"AEBENCH_REAL_SHELL": "/bin/bash",
				"AEBENCH_COMMAND_SOCKET": str(broker.socket_path),
			},
			timeout=60,
			check=False,
		)

	assert result.stdout == "piped-in\n"


def test_large_output_streams_without_loss(shim: Path, output_dir: Path) -> None:
	line_count = 20_000
	with _broker(output_dir) as broker:
		result = _run(shim, ["-c", f"seq 1 {line_count}"], socket_path=broker.socket_path)

	assert result.returncode == 0
	assert result.stdout.count("\n") == line_count

	[record] = _records(output_dir)
	assert record["stdout_state"] == "captured"
	assert (output_dir / record["stdout_path"]).read_text() == result.stdout


def test_the_capture_matches_what_the_agent_saw(shim: Path, output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		result = _run(shim, ["-c", "seq 1 1000"], socket_path=broker.socket_path)

	[record] = _records(output_dir)
	assert record["stdout_state"] == "captured"
	assert (output_dir / record["stdout_path"]).read_text() == result.stdout


def test_a_denied_command_never_runs(shim: Path, output_dir: Path, tmp_path: Path) -> None:
	marker = tmp_path / "should-not-exist"
	policy = PatternDenyPolicy.from_strings((r"should-not-exist",), reason="blocked for the test")
	with _broker(output_dir, interceptors=(policy,)) as broker:
		result = _run(shim, ["-c", f"touch {marker}"], socket_path=broker.socket_path)

	assert result.returncode == 126
	assert "blocked for the test" in result.stderr
	assert not marker.exists()

	[record] = _records(output_dir)
	assert record["allowed"] is False
	assert record["denied_by"] == "pattern_deny"


def test_a_nested_shell_is_linked_to_its_parent(shim: Path, output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		result = _run(shim, ["-c", f"{shim} -c 'echo from-nested'"], socket_path=broker.socket_path)

	assert result.returncode == 0
	assert result.stdout == "from-nested\n"

	records = {record["command_id"]: record for record in _records(output_dir)}
	assert records["cmd_000001"]["parent_command_id"] is None
	assert records["cmd_000002"]["parent_command_id"] == "cmd_000001"
	assert records["cmd_000002"]["exit_code"] == 0
	assert all(not record["incomplete"] for record in records.values())


def test_the_command_still_runs_when_no_broker_is_listening(shim: Path, tmp_path: Path) -> None:
	result = _run(shim, ["-c", "echo still-works"], socket_path=tmp_path / "missing.sock")

	assert result.returncode == 0
	assert result.stdout == "still-works\n"


def test_the_command_still_runs_when_the_socket_is_unset(shim: Path) -> None:
	result = _run(shim, ["-c", "echo unmonitored"], socket_path=None)

	assert result.returncode == 0
	assert result.stdout == "unmonitored\n"


def test_environment_values_never_reach_the_trace(shim: Path, output_dir: Path) -> None:
	with _broker(output_dir) as broker:
		_run(
			shim,
			["-c", "true"],
			socket_path=broker.socket_path,
			extra_env={"ANTHROPIC_API_KEY": "sk-super-secret"},
		)

	[record] = _records(output_dir)
	raw = (output_dir / "commands.jsonl").read_text()
	assert "ANTHROPIC_API_KEY" in record["env_keys"]
	assert "sk-super-secret" not in raw
