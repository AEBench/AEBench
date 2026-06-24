"""Tests for oracle runtime selection and execution."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evaluator.oracles.oracle_checks_runtime import (
	DockerRuntimeCheckExecutor,
	LocalRuntimeCheckExecutor,
	SessionRuntimeCheckExecutor,
	build_oracle_runtime_registry,
	build_path_mounts,
)
from models import (
	DockerImageOracleTargetConfig,
	OracleConfig,
	OracleTargetConfig,
	RuntimeMode,
)


class _FakeRuntimeBackend:
	"""Records commands issued through a session runtime executor."""

	path_separator = ":"

	def __init__(self) -> None:
		self.calls: list[dict[str, Any]] = []
		self.exception: Exception | None = None
		self.returncode = 0
		self.stdout = "runtime stdout"
		self.stderr = ""

	def run_process(
		self,
		cmd: Sequence[str],
		*,
		cwd: str,
		env: Mapping[str, str] | None,
		timeout: float,
	) -> subprocess.CompletedProcess[str]:
		self.calls.append(
			{
				"cmd": list(cmd),
				"cwd": cwd,
				"env": None if env is None else dict(env),
				"timeout": timeout,
			}
		)

		if self.exception is not None:
			raise self.exception

		return subprocess.CompletedProcess(
			args=list(cmd),
			returncode=self.returncode,
			stdout=self.stdout,
			stderr=self.stderr,
		)


def _recorded_runtime(
	mode: RuntimeMode | str,
	*,
	saved_image: str | None = None,
	image: str | None = None,
) -> Any:
	return SimpleNamespace(
		runtime=SimpleNamespace(
			mode=mode,
			saved_image=saved_image,
			image=image,
		)
	)


def _oracle_context(
	tmp_path: Path,
	*,
	extra_targets: Mapping[str, OracleTargetConfig] | None = None,
	runtime_result: Any = None,
	runtime_session: Any = None,
	runtime_backend: Any = None,
) -> Any:
	"""Builds an oracle runtime context for executor unit tests."""
	oracle_config = OracleConfig(
		targets=dict(extra_targets or {}),
	)

	case_dir = tmp_path / "case"
	artifact_dir = tmp_path / "artifact"
	workspace_dir = tmp_path / "workspace"
	output_dir = tmp_path / "output"
	refs_dir = case_dir / "refs"

	for path in (
		case_dir,
		refs_dir,
		artifact_dir,
		workspace_dir,
		output_dir,
	):
		path.mkdir(parents=True, exist_ok=True)

	return SimpleNamespace(
		case_dir=case_dir,
		artifact_dir=artifact_dir,
		workspace_dir=workspace_dir,
		output_dir=output_dir,
		oracle_targets=oracle_config.targets,
		oracle_phase_targets=oracle_config.phase_targets,
		runtime_result=runtime_result,
		runtime_session=runtime_session,
		runtime_backend=runtime_backend,
		runtime_registry=None,
	)


def _session_executor(
	context: Any,
	backend: _FakeRuntimeBackend,
) -> SessionRuntimeCheckExecutor:
	return SessionRuntimeCheckExecutor(
		session=object(),
		runtime_backend=backend,
		path_mounts=build_path_mounts(context),
		default_cwd=context.workspace_dir,
	)


def test_task_target_active_session_takes_precedence_over_recorded_runtime(
	tmp_path: Path,
) -> None:
	backend = _FakeRuntimeBackend()
	context = _oracle_context(
		tmp_path,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			image="recorded-image:latest",
		),
		runtime_session=object(),
		runtime_backend=backend,
	)

	registry = build_oracle_runtime_registry(context)
	executor = registry.executor_for("task")

	assert isinstance(executor, SessionRuntimeCheckExecutor)


def test_missing_runtime_result_uses_local_executor(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		runtime_result=None,
	)

	registry = build_oracle_runtime_registry(context)
	executor = registry.executor_for("task")

	assert isinstance(executor, LocalRuntimeCheckExecutor)


def test_recorded_local_runtime_uses_local_executor(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		runtime_result=_recorded_runtime(
			RuntimeMode.LOCAL
		),
	)

	registry = build_oracle_runtime_registry(context)
	executor = registry.executor_for("task")

	assert isinstance(executor, LocalRuntimeCheckExecutor)


def test_recorded_docker_runtime_falls_back_to_original_image(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			saved_image=None,
			image="original-image:latest",
		),
	)

	registry = build_oracle_runtime_registry(context)
	executor = registry.executor_for("task")

	assert isinstance(
		executor,
		DockerRuntimeCheckExecutor,
	)
	assert executor._image == "original-image:latest"


def test_recorded_docker_runtime_prefers_saved_image(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			saved_image="saved-image:latest",
			image="original-image:latest",
		),
	)

	registry = build_oracle_runtime_registry(context)
	executor = registry.executor_for("task")

	assert isinstance(executor, DockerRuntimeCheckExecutor)
	assert executor._image == "saved-image:latest"


def test_recorded_docker_runtime_without_image_raises(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			saved_image=None,
			image=None,
		),
	)

	registry = build_oracle_runtime_registry(context)

	with pytest.raises(RuntimeError):
		registry.executor_for("task")


def test_unsupported_recorded_runtime_mode_raises(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		runtime_result=_recorded_runtime("unsupported"),
	)

	registry = build_oracle_runtime_registry(context)

	with pytest.raises(RuntimeError):
		registry.executor_for("task")


def test_build_path_mounts_includes_standard_case_paths(
	tmp_path: Path,
) -> None:
	context = _oracle_context(tmp_path)

	mounts = build_path_mounts(context)

	by_runtime_path = {str(mount.runtime_root): mount.host_root for mount in mounts}

	assert by_runtime_path["/workspace"] == context.workspace_dir.resolve()
	assert by_runtime_path["/case"] == context.case_dir.resolve()
	assert by_runtime_path["/refs"] == (context.case_dir / "refs").resolve()
	assert by_runtime_path["/artifact"] == context.artifact_dir.resolve()
	assert by_runtime_path["/output"] == context.output_dir.resolve()


def test_build_path_mounts_orders_nested_paths_first(
	tmp_path: Path,
) -> None:
	context = _oracle_context(tmp_path)

	mounts = build_path_mounts(context)
	depths = [len(mount.host_root.parts) for mount in mounts]

	assert depths == sorted(depths, reverse=True)


def test_local_executor_merges_environment_overrides(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("AEBENCH_PARENT_VALUE", "parent")
	executor = LocalRuntimeCheckExecutor(default_cwd=tmp_path)

	result = executor.run_process_capture(
		cmd=(
			sys.executable,
			"-c",
			"import os; "
			"print(os.environ['AEBENCH_PARENT_VALUE']); "
			"print(os.environ['AEBENCH_CHILD_VALUE'])",
		),
		cwd=None,
		env={"AEBENCH_CHILD_VALUE": "child"},
		timeout_seconds=5.0,
	)

	assert result.returncode == 0
	assert result.stdout.splitlines() == [
		"parent",
		"child",
	]
	assert result.stderr == ""
	assert result.timed_out is False


def test_session_executor_translates_cwd_and_shell_command(
	tmp_path: Path,
) -> None:
	context = _oracle_context(tmp_path)
	backend = _FakeRuntimeBackend()
	executor = _session_executor(context, backend)

	result = executor.run_process_capture(
		cmd=("printf", "hello world"),
		cwd=context.workspace_dir / "nested",
		env={"AEBENCH_VALUE": "configured"},
		timeout_seconds=3.0,
		use_shell=True,
	)

	assert result.returncode == 0
	assert result.stdout == "runtime stdout"
	assert result.timed_out is False
	assert backend.calls == [
		{
			"cmd": [
				"sh",
				"-lc",
				"printf 'hello world'",
			],
			"cwd": "/workspace/nested",
			"env": {"AEBENCH_VALUE": "configured"},
			"timeout": 3.0,
		}
	]


def test_session_executor_rejects_string_without_shell(
	tmp_path: Path,
) -> None:
	context = _oracle_context(tmp_path)
	backend = _FakeRuntimeBackend()
	executor = _session_executor(context, backend)

	with pytest.raises(
		TypeError,
		match="use_shell=False requires cmd to be a sequence",
	):
		executor.run_process_capture(
			cmd="printf hello",
			cwd=None,
			env=None,
			timeout_seconds=3.0,
			use_shell=False,
		)

	assert backend.calls == []


def test_session_executor_converts_timeout_to_proc_result(
	tmp_path: Path,
) -> None:
	context = _oracle_context(tmp_path)
	backend = _FakeRuntimeBackend()
	backend.exception = subprocess.TimeoutExpired(
		cmd=["sleep", "10"],
		timeout=0.1,
		output="partial stdout",
		stderr="partial stderr",
	)
	executor = _session_executor(context, backend)

	result = executor.run_process_capture(
		cmd=("sleep", "10"),
		cwd=None,
		env=None,
		timeout_seconds=0.1,
	)

	assert result.returncode is None
	assert result.stdout == "partial stdout"
	assert result.stderr == "partial stderr"
	assert result.timed_out is True


def test_docker_executor_starts_container_lazily_and_reuses_it(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	context = _oracle_context(tmp_path)
	calls: list[list[str]] = []

	def fake_run(
		cmd: Sequence[str],
		**_kwargs: Any,
	) -> subprocess.CompletedProcess[str]:
		command = list(cmd)
		calls.append(command)

		if command[:3] == ["docker", "run", "-d"]:
			return subprocess.CompletedProcess(
				args=command,
				returncode=0,
				stdout="container-id\n",
				stderr="",
			)

		return subprocess.CompletedProcess(
			args=command,
			returncode=0,
			stdout="",
			stderr="",
		)

	monkeypatch.setattr(subprocess, "run", fake_run)

	executor = DockerRuntimeCheckExecutor(
		image="oracle-image:latest",
		path_mounts=build_path_mounts(context),
		default_cwd=context.workspace_dir,
	)

	assert calls == []

	assert executor.path_exists(context.workspace_dir) is True
	assert executor.path_is_dir(context.workspace_dir) is True

	container_starts = [command for command in calls if command[:3] == ["docker", "run", "-d"]]
	container_execs = [command for command in calls if command[:2] == ["docker", "exec"]]

	assert len(container_starts) == 1
	assert len(container_execs) == 2

	start_command = container_starts[0]
	working_directory_index = start_command.index("-w")
	assert start_command[working_directory_index + 1] == "/workspace"


def test_docker_image_target_uses_configured_image_and_working_dir(
	tmp_path: Path,
) -> None:
	context = _oracle_context(
		tmp_path,
		extra_targets={
			"golf": DockerImageOracleTargetConfig(
				image="golf",
				working_dir="/usr/app",
			),
		},
		runtime_result=_recorded_runtime(
			RuntimeMode.DOCKER,
			image="task-image:latest",
		),
		runtime_session=object(),
		runtime_backend=_FakeRuntimeBackend(),
	)

	registry = build_oracle_runtime_registry(context)
	executor = registry.executor_for("golf")

	assert isinstance(executor, DockerRuntimeCheckExecutor)
	assert executor._image == "golf"
	assert str(executor._runtime_cwd) == "/usr/app"