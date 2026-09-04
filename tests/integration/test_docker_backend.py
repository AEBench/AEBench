from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from evaluator.oracles.oracle_checks_runtime import DockerRuntimeCheckExecutor, RuntimePath
from models import RuntimeConfig, RuntimeMode, TaskConfig
from runtime.agent_runner import clear_agent_support_dir, prepare_agent_runtime
from runtime.backend import DockerRuntime

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


def test_stopped_container_can_be_committed_for_detached_scoring(tmp_path: Path) -> None:
	_require_docker_image()

	workspace = tmp_path / "workspace"
	agent_support_dir = tmp_path / "agent-support"
	workspace.mkdir()
	agent_support_dir.mkdir()
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
		host_agent_support_dir=agent_support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
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


def test_task_oracle_uses_agent_home_from_saved_runtime(tmp_path: Path) -> None:
	_require_docker_image()

	workspace = tmp_path / "workspace"
	support_dir = tmp_path / "agent-support"
	workspace.mkdir()
	support_dir.mkdir()
	(support_dir / "credential").write_text("secret", encoding="utf-8")
	task = TaskConfig(
		id="docker-agent-home-test",
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
		host_agent_support_dir=support_dir,
		runtime_agent_support_dir="/run/aebench-agent",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
	)
	runtime = DockerRuntime(image=_IMAGE)
	executor: DockerRuntimeCheckExecutor | None = None
	saved_image: str | None = None

	try:
		runtime.prepare(session)  # type: ignore[arg-type]
		prepare_agent_runtime(runtime)
		install = runtime.run_process(
			[
				"runuser",
				"--user",
				"agent",
				"--preserve-environment",
				"--",
				"python3",
				"-c",
				(
					"import pathlib, site; "
					"target = pathlib.Path(site.getusersitepackages()); "
					"target.mkdir(parents=True, exist_ok=True); "
					"(target / 'aebench_snapshot_dependency.py').write_text('VALUE = 42\\n')"
				),
			],
			env={"HOME": "/home/agent"},
		)
		assert install.returncode == 0, install.stderr

		clear_agent_support_dir(runtime, "/run/aebench-agent")
		assert list(support_dir.iterdir()) == []
		runtime.stop(session)  # type: ignore[arg-type]
		saved_image = runtime.snapshot(session)  # type: ignore[arg-type]
		assert saved_image is not None

		executor = DockerRuntimeCheckExecutor(
			image=saved_image,
			path_mounts=(),
			default_cwd=workspace,
			runtime_cwd=PurePosixPath("/home/agent"),
			user="agent",
			home="/home/agent",
		)
		result = executor.run_process_capture(
			cmd=(
				"python3",
				"-c",
				"import aebench_snapshot_dependency as dep; print(dep.VALUE)",
			),
			cwd=None,
			env=None,
			timeout_seconds=10,
		)
		assert result.returncode == 0, result.stderr
		assert result.stdout.strip() == "42"
		assert (
			executor.path_exists(RuntimePath.from_parts("/run/aebench-agent/credential")) is False
		)
	finally:
		if executor is not None:
			executor.close()
		runtime.cleanup(session)  # type: ignore[arg-type]
		if saved_image is not None:
			subprocess.run(
				["docker", "rmi", "-f", saved_image],
				capture_output=True,
				text=True,
				check=False,
			)
