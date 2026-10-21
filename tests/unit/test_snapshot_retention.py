"""Keeping the post-agent Docker snapshot, and the time allowed to create it."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator.loader import load_case_spec
from models import RuntimeConfig, RuntimeInfo, RuntimeMode, TaskConfig
from runtime.backend import DockerRuntime
from runtime.reporting import snapshot_status


def _session(runtime: RuntimeConfig) -> SimpleNamespace:
	return SimpleNamespace(
		task_id="test",
		run_spec=TaskConfig(id="test", runtime=runtime),
		host_workspace=Path("/host/workspace"),
		runtime_workspace="/repo",
		runtime_agent_user="agent",
		runtime_agent_home="/home/agent",
	)


def _fake_docker(
	monkeypatch: pytest.MonkeyPatch,
	*,
	commit_returncode: int = 0,
	failing: str | None = None,
	volumes: str = "null",
) -> list[tuple[list[str], dict[str, object]]]:
	calls: list[tuple[list[str], dict[str, object]]] = []
	stdout = {"create": "copy-id\n", "image": volumes}

	def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
		calls.append((cmd, kwargs))
		returncode = commit_returncode if cmd[:2] == ["docker", "commit"] else 0
		if cmd[1] == failing:
			returncode = 1
		return subprocess.CompletedProcess(cmd, returncode, stdout.get(cmd[1], ""), "failed")

	monkeypatch.setattr(subprocess, "run", run)
	return calls


def _stopped_container() -> DockerRuntime:
	return DockerRuntime(
		container_id="container-id",
		container_name="container-name",
		resolved_image="aebench-agent:latest",
		container_stopped=True,
	)


def test_docker_runtime_keeps_snapshot_and_allows_an_hour_by_default() -> None:
	runtime = RuntimeConfig(mode="docker")

	assert runtime.keep_committed_snapshot is True
	assert runtime.snapshot_timeout_seconds == 3600.0


def test_case_toml_overrides_snapshot_settings(tmp_path: Path) -> None:
	case_dir = tmp_path / "test_case"
	(case_dir / "refs").mkdir(parents=True)
	(case_dir / "artifact").mkdir()
	(case_dir / "artifact" / "README.md").write_text("# test artifact\n", encoding="utf-8")
	(case_dir / "oracles").mkdir()
	(case_dir / "oracles" / "env_setup.py").write_text(
		"from evaluator.oracles.bases import CaseOracleEnvSetupBase\n"
		"class OracleEnvSetup(CaseOracleEnvSetupBase):\n"
		"	def requirements(self): return []\n",
		encoding="utf-8",
	)
	(case_dir / "case.toml").write_text(
		textwrap.dedent(
			"""\
			id = "test_case"

			[case_brief]
			core_claim = "Verify the artifact builds correctly."
			acceptable_evidence = "The binary exists and runs."
			allowed_tolerance = "None."

			[run]
			id = "test_case"
			[run.runtime]
			mode = "docker"
			keep_committed_snapshot = false
			snapshot_timeout_seconds = 120.0

			[oracle]
			phases = ["env_setup"]
			"""
		),
		encoding="utf-8",
	)

	runtime = load_case_spec(case_dir).run.runtime

	assert runtime.keep_committed_snapshot is False
	assert runtime.snapshot_timeout_seconds == 120.0


@pytest.mark.parametrize("timeout", [3600.0, 120.0])
def test_snapshot_timeout_is_passed_to_docker_commit(
	monkeypatch: pytest.MonkeyPatch, timeout: float
) -> None:
	calls = _fake_docker(monkeypatch)
	runtime = _stopped_container()

	tag = runtime.snapshot(
		_session(
			RuntimeConfig(
				mode="docker", snapshot_timeout_seconds=timeout, include_workspace_in_snapshot=False
			)
		)  # type: ignore[arg-type]
	)

	assert calls == [(["docker", "commit", "container-id", tag], calls[0][1])]
	assert calls[0][1]["timeout"] == timeout


def test_successful_snapshot_is_kept_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch)
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker", include_workspace_in_snapshot=False))

	tag = runtime.snapshot(session)  # type: ignore[arg-type]
	runtime.cleanup(session)  # type: ignore[arg-type]

	# The container is still removed; the committed image is not.
	assert [cmd for cmd, _ in calls] == [
		["docker", "commit", "container-id", tag],
		["docker", "rm", "-f", "container-id"],
	]
	assert runtime.container_removed is True
	assert runtime.saved_image == tag
	result = runtime.runtime_result(session)  # type: ignore[arg-type]
	assert result.saved_image == tag
	assert result.snapshot_failed is False


def test_deleting_the_image_removes_the_committed_snapshot(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	calls = _fake_docker(monkeypatch)
	runtime = _stopped_container()
	# What --delete-image applies to the case's runtime configuration.
	session = _session(
		RuntimeConfig(
			mode="docker", keep_committed_snapshot=False, include_workspace_in_snapshot=False
		)
	)

	tag = runtime.snapshot(session)  # type: ignore[arg-type]
	runtime.cleanup(session)  # type: ignore[arg-type]

	assert [cmd for cmd, _ in calls] == [
		["docker", "commit", "container-id", tag],
		["docker", "rm", "-f", "container-id"],
		["docker", "rmi", "-f", tag],
	]
	assert runtime.saved_image is None


def test_failed_snapshot_leaves_no_image_to_delete(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch, commit_returncode=1)
	runtime = _stopped_container()
	session = _session(
		RuntimeConfig(
			mode="docker", keep_committed_snapshot=False, include_workspace_in_snapshot=False
		)
	)

	with pytest.raises(RuntimeError, match="docker commit failed"):
		runtime.snapshot(session)  # type: ignore[arg-type]
	runtime.cleanup(session)  # type: ignore[arg-type]

	# Cleanup is otherwise unchanged: the container is removed, and no rmi is attempted.
	assert [cmd[:2] for cmd, _ in calls] == [["docker", "commit"], ["docker", "rm"]]
	assert runtime.saved_image is None


def test_failed_snapshot_is_recorded_in_the_runtime_result(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	_fake_docker(monkeypatch, commit_returncode=1)
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker"))

	with pytest.raises(RuntimeError):
		runtime.snapshot(session)  # type: ignore[arg-type]
	runtime.cleanup(session)  # type: ignore[arg-type]

	result = runtime.runtime_result(session)  # type: ignore[arg-type]
	assert result.snapshot_failed is True
	assert result.container_id == "container-id"
	# Cleanup still removes the container after a failed snapshot.
	assert result.container_removed is True
	assert snapshot_status(result) == "failed; the container was removed"


def test_timed_out_snapshot_is_recorded_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
	def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
		raise subprocess.TimeoutExpired(cmd, float(kwargs["timeout"]))  # type: ignore[arg-type]

	monkeypatch.setattr(subprocess, "run", run)
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker"))

	with pytest.raises(subprocess.TimeoutExpired):
		runtime.snapshot(session)  # type: ignore[arg-type]

	assert runtime.runtime_result(session).snapshot_failed is True  # type: ignore[arg-type]


def test_snapshot_status_names_a_kept_container_and_how_to_retry() -> None:
	kept = RuntimeInfo(mode=RuntimeMode.DOCKER, container_id="abc123", snapshot_failed=True)

	assert snapshot_status(kept) == (
		"failed; container `abc123` was kept (retry: `docker commit abc123 <tag>`)"
	)


def test_workspace_is_copied_into_the_snapshot_by_default() -> None:
	assert RuntimeConfig(mode="docker").include_workspace_in_snapshot is True


def test_workspace_copy_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch)
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker", include_workspace_in_snapshot=False))

	runtime.snapshot(session)  # type: ignore[arg-type]

	assert [cmd[1] for cmd, _ in calls] == ["commit"]
	assert runtime.workspace_in_snapshot is False


def test_workspace_is_copied_into_the_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch)
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker"))

	tag = runtime.snapshot(session)  # type: ignore[arg-type]

	assert [cmd for cmd, _ in calls] == [
		["docker", "commit", "container-id", tag],
		["docker", "image", "inspect", "--format", "{{json .Config.Volumes}}", tag],
		["docker", "create", tag],
		["docker", "cp", "-a", "/host/workspace/.", "copy-id:/repo"],
		["docker", "commit", "copy-id", tag],
		["docker", "rm", "-f", "copy-id"],
	]
	result = runtime.runtime_result(session)  # type: ignore[arg-type]
	assert result.saved_image == tag
	assert result.workspace_in_snapshot is True


def test_failed_workspace_copy_keeps_the_plain_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch, failing="cp")
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker"))

	tag = runtime.snapshot(session)  # type: ignore[arg-type]

	# The copy is not committed over the tag, and the temporary container is removed.
	assert [cmd[:3] for cmd, _ in calls][-2:] == [["docker", "cp", "-a"], ["docker", "rm", "-f"]]
	result = runtime.runtime_result(session)  # type: ignore[arg-type]
	assert result.saved_image == tag
	assert result.snapshot_failed is False
	assert result.workspace_in_snapshot is False


def test_workspace_declared_as_a_volume_is_not_copied(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch, volumes='{"/repo":{}}')
	runtime = _stopped_container()
	session = _session(RuntimeConfig(mode="docker"))

	runtime.snapshot(session)  # type: ignore[arg-type]

	assert [cmd[1] for cmd, _ in calls] == ["commit", "image"]
	assert runtime.workspace_in_snapshot is False
