"""The shim and broker against a real container and the real Rust binary.

Skipped unless the agent image is built. This is the only test that proves the
pieces fit: the binary runs under the image's glibc, the socket survives the
bind mount, and the container's unprivileged agent can reach it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from models import RuntimeConfig, RuntimeMode, TaskConfig
from runtime.backend import DockerRuntime
from runtime.monitoring import BrokerProcess, install_shim, verify_monitoring

_IMAGE = "aebench-agent:latest"


def _require_docker_image() -> None:
	if shutil.which("docker") is None:
		pytest.skip("docker CLI is unavailable")
	if subprocess.run(["docker", "info"], capture_output=True, text=True, check=False).returncode:
		pytest.skip("docker daemon is unavailable")
	if subprocess.run(
		["docker", "image", "inspect", _IMAGE], capture_output=True, text=True, check=False
	).returncode:
		pytest.skip(f"build {_IMAGE} before running Docker integration tests")


def _records(broker: BrokerProcess) -> list[dict[str, Any]]:
	if not broker.trace_path.is_file():
		return []
	return [
		json.loads(line)
		for line in broker.trace_path.read_text(encoding="utf-8").splitlines()
		if line.strip()
	]


def _wait_for_records(broker: BrokerProcess, count: int, *, timeout: float = 15.0) -> None:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if broker.command_count() >= count:
			return
		time.sleep(0.05)
	raise AssertionError(f"expected {count} records, saw {broker.command_count()}")


def test_a_monitored_container_records_the_agent_user_shells(tmp_path: Path) -> None:
	_require_docker_image()

	workspace = tmp_path / "workspace"
	workspace.mkdir()
	agent_support_dir = tmp_path / "agent-support"
	agent_support_dir.mkdir()

	broker = BrokerProcess(
		trace_dir=tmp_path / "trace",
		workspace_dir=workspace,
		socket_root=tmp_path / "sockets",
	)
	broker.start()

	task = TaskConfig(
		id="monitoring-docker",
		runtime=RuntimeConfig(mode=RuntimeMode.DOCKER, image=_IMAGE),
	)
	session = SimpleNamespace(
		task_id=task.id,
		run_spec=task,
		settings=SimpleNamespace(default_docker_image=_IMAGE),
		host_workspace=workspace,
		runtime_workspace="/repo",
		host_refs=None,
		host_agent_support_dir=agent_support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
		host_command_socket_dir=broker.socket_dir,
		runtime_command_socket_dir="/run/aebench",
	)
	runtime = DockerRuntime(image=_IMAGE)

	try:
		runtime.prepare(session)  # type: ignore[arg-type]

		# Dormant until installed: `aebench case run` must see a stock image.
		before = runtime.run_process(["bash", "-c", "echo $BASH_VERSION"])
		assert before.returncode == 0 and before.stdout.strip()
		assert broker.command_count() == 0

		install_shim(runtime)  # type: ignore[arg-type]
		verify_monitoring(runtime, broker)  # type: ignore[arg-type]

		probe = _records(broker)
		assert len(probe) == 1
		assert probe[0]["argv"][0] == "/bin/bash"
		assert probe[0]["exit_code"] == 0

		# The shim is transparent: status, stdout and stdin all pass through.
		result = runtime.run_process(
			[
				"runuser",
				"--user",
				"agent",
				"--",
				"/bin/bash",
				"-c",
				"echo out; echo err >&2; exit 3",
			]
		)
		assert result.returncode == 3
		assert result.stdout.strip() == "out"
		assert result.stderr.strip() == "err"

		_wait_for_records(broker, 2)
		record = _records(broker)[1]
		assert record["exit_code"] == 3
		assert record["complete"] is True
		captured = broker.trace_dir / record["stdout"]["path"]
		assert captured.read_bytes() == b"out\n"

		# A shell started by another process is monitored too, which is what
		# makes make recipes and shebang scripts visible.
		nested = runtime.run_process(
			["runuser", "--user", "agent", "--", "/bin/bash", "-c", "bash -c 'exit 5'"]
		)
		assert nested.returncode == 5
		_wait_for_records(broker, 4)

		# The shim stays in place for the snapshot, so the trace keeps growing
		# for as long as the container lives.
		assert broker.command_count() >= 4
	finally:
		runtime.cleanup(session)  # type: ignore[arg-type]
		broker.stop()


def test_the_shim_runs_unmonitored_with_no_socket_at_all(tmp_path: Path) -> None:
	"""Fail-open is the shim's core promise, and it is why the shell is never restored.

	The container started from the committed snapshot -- the one the oracle
	scores -- has no socket mounted anywhere, exactly as here. Its `bash -c`
	checks run through the shim and behave normally, so putting the real shell
	back before committing would buy nothing.
	"""
	_require_docker_image()

	workspace = tmp_path / "workspace"
	workspace.mkdir()
	agent_support_dir = tmp_path / "agent-support"
	agent_support_dir.mkdir()

	task = TaskConfig(
		id="monitoring-fail-open",
		runtime=RuntimeConfig(mode=RuntimeMode.DOCKER, image=_IMAGE),
	)
	session = SimpleNamespace(
		task_id=task.id,
		run_spec=task,
		settings=SimpleNamespace(default_docker_image=_IMAGE),
		host_workspace=workspace,
		runtime_workspace="/repo",
		host_refs=None,
		host_agent_support_dir=agent_support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
		host_command_socket_dir=None,
		runtime_command_socket_dir=None,
	)
	runtime = DockerRuntime(image=_IMAGE)

	try:
		runtime.prepare(session)  # type: ignore[arg-type]
		install_shim(runtime)  # type: ignore[arg-type]

		assert runtime.run_process(["sh", "-c", "! test -e /run/aebench"]).returncode == 0

		# Exit status and output pass through untouched, as the oracle needs.
		result = runtime.run_process(["bash", "-c", "echo still-works; exit 9"])
		assert result.returncode == 9
		assert result.stdout.strip() == "still-works"
		assert runtime.run_process(["sh", "-lc", "command -v bash"]).returncode == 0
	finally:
		runtime.cleanup(session)  # type: ignore[arg-type]
