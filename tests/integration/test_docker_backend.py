from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from models import RuntimeConfig, RuntimeMode, TaskConfig
from runtime.backend import DockerRuntime

_IMAGE = "aebench-agent:latest"


def test_stopped_container_can_be_committed_for_detached_scoring(tmp_path: Path) -> None:
	if shutil.which("docker") is None:
		pytest.skip("docker CLI is unavailable")
	if subprocess.run(["docker", "info"], capture_output=True, text=True, check=False).returncode:
		pytest.skip("docker daemon is unavailable")
	if subprocess.run(
		["docker", "image", "inspect", _IMAGE], capture_output=True, text=True, check=False
	).returncode:
		pytest.skip(f"build {_IMAGE} before running Docker integration tests")

	workspace = tmp_path / "workspace"
	agent_home = tmp_path / "agent-home"
	workspace.mkdir()
	agent_home.mkdir()
	task = TaskConfig(
		id="docker-snapshot-test",
		runtime=RuntimeConfig(
			mode=RuntimeMode.DOCKER,
			image=_IMAGE,
			keep_committed_snapshot=True,
		),
	)
	session = SimpleNamespace(
		task_id=task.id,
		run_spec=task,
		settings=SimpleNamespace(default_docker_image=_IMAGE),
		host_workspace=workspace,
		runtime_workspace="/repo",
		host_refs=None,
		host_agent_home=agent_home,
		runtime_agent_home="/home/agent",
	)
	runtime = DockerRuntime(image=_IMAGE)
	saved_image: str | None = None

	try:
		runtime.prepare(session)  # type: ignore[arg-type]
		result = runtime.run_process(
			["sh", "-c", "printf snapshot-ok > /opt/aebench-snapshot-marker"]
		)
		assert result.returncode == 0

		runtime.stop(session)  # type: ignore[arg-type]
		saved_image = runtime.snapshot(session)  # type: ignore[arg-type]
		assert saved_image is not None
		probe = subprocess.run(
			["docker", "run", "--rm", saved_image, "cat", "/opt/aebench-snapshot-marker"],
			capture_output=True,
			text=True,
			check=False,
		)
		assert probe.returncode == 0
		assert probe.stdout == "snapshot-ok"
	finally:
		runtime.cleanup(session)  # type: ignore[arg-type]
		if saved_image is not None:
			subprocess.run(
				["docker", "rmi", "-f", saved_image],
				capture_output=True,
				text=True,
				check=False,
			)
