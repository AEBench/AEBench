from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from config import AppState, resolve_settings
from models import (
	AgentResult,
	CaseStatus,
	OracleResult,
	OracleStatus,
	RunOptions,
	RuntimeInfo,
	RuntimeMode,
)
from project_config import load_project_config
from runtime.case_runner import run_case

_FIXTURE = Path(__file__).parent / "mock-case" / "fixture"


def test_case_runner_executes_agent_then_oracle(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))
	output_dir = tmp_path / "output"
	events: list[str] = []
	runtime_paths: dict[str, str] = {}
	clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}

	class FakeDateTime:
		@staticmethod
		def now(_timezone: timezone) -> datetime:
			return clock["now"]

	def fake_agent(
		*_args: Any,
		output_path: Path,
		model: str,
		runtime_home: str,
		runtime_support_dir: str,
		**_kwargs: Any,
	) -> AgentResult:
		events.append("agent")
		runtime_paths.update(home=runtime_home, support=runtime_support_dir)
		output_path.write_text('{"type":"result"}\n', encoding="utf-8")
		clock["now"] += timedelta(seconds=2)
		return AgentResult(model=model, exit_code=0, reasoning_effort="high")

	def clear_support_dir(*_args: Any, **_kwargs: Any) -> None:
		clock["now"] += timedelta(seconds=3)

	def record_output_dir(path: Path) -> None:
		assert path.is_dir()
		events.append(f"output:{path}")

	monkeypatch.setattr("runtime.case_runner.run_agent", fake_agent)
	monkeypatch.setattr("runtime.case_runner.clear_agent_support_dir", clear_support_dir)
	monkeypatch.setattr("runtime.case_runner.datetime", FakeDateTime)
	result = run_case(
		context,
		project / "bundles" / "mock_apt_case",
		options=RunOptions(agent_type="codex", model_name="gpt-test", allow_unsafe_local=True),
		save_path=output_dir,
		on_output_dir=record_output_dir,
	)

	assert events == [f"output:{output_dir}", "agent"]
	assert result.status == CaseStatus.SUCCESS
	assert result.oracle_result.score == 4
	assert result.runtime_result.agent_kind == "codex"
	assert result.runtime_result.agent.reasoning_effort == "high"
	assert result.runtime_result.duration_ms == 2_000
	assert runtime_paths["home"] == str(Path.home())
	assert runtime_paths["support"].endswith("/agent-support")
	assert runtime_paths["support"] != runtime_paths["home"]
	assert (output_dir / "runner_output.log").is_file()
	prompt = next(output_dir.glob("aebench_prompt_*.md")).read_text(encoding="utf-8")
	assert "Acceptable Evidence" in prompt
	assert "Allowed Tolerance" in prompt
	assert "Keep the installed zip executable available for inspection." in prompt
	run_record = json.loads((output_dir / "result.jsonl").read_text(encoding="utf-8"))
	assert run_record["agent"]["reasoning_effort"] == "high"
	assert (output_dir / "case_result.json").is_file()


def test_case_runner_rejects_unisolated_local_agent(tmp_path: Path) -> None:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))

	with pytest.raises(ValueError, match="no process isolation"):
		run_case(
			context,
			project / "bundles" / "mock_apt_case",
			options=RunOptions(agent_type="codex", model_name="gpt-test"),
		)


def test_case_runner_rejects_incomplete_local_source(tmp_path: Path) -> None:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	manifest = project / "bundles" / "mock_apt_case" / "case.toml"
	manifest.write_text(
		manifest.read_text(encoding="utf-8").replace('path = "artifact"\n', ""),
		encoding="utf-8",
	)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))

	with pytest.raises(RuntimeError, match="no usable upstream source \\(local\\)"):
		run_case(
			context,
			project / "bundles" / "mock_apt_case",
			options=RunOptions(
				agent_type="codex",
				model_name="gpt-test",
				allow_unsafe_local=True,
			),
		)


def test_case_runner_scores_workspace_after_agent_timeout(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))

	def timed_out_agent(*_args: Any, output_path: Path, model: str, **_kwargs: Any) -> AgentResult:
		output_path.write_text("agent reached its time limit\n", encoding="utf-8")
		return AgentResult(model=model, exit_code=124)

	monkeypatch.setattr("runtime.case_runner.run_agent", timed_out_agent)
	result = run_case(
		context,
		project / "bundles" / "mock_apt_case",
		options=RunOptions(agent_type="codex", model_name="gpt-test", allow_unsafe_local=True),
		save_path=tmp_path / "output",
	)

	assert result.status == CaseStatus.ERROR
	assert result.runtime_result.status.value == "error"
	assert result.runtime_result.agent.exit_code == 124
	assert result.oracle_result.score == 4


def test_docker_case_scores_stopped_snapshot_not_live_session(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	manifest = project / "bundles" / "mock_apt_case" / "case.toml"
	manifest.write_text(
		manifest.read_text(encoding="utf-8").replace(
			'mode = "local"\ntimeout_ms',
			'mode = "docker"\nimage = "aebench-agent:latest"\ntimeout_ms',
		),
		encoding="utf-8",
	)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))
	events: list[str] = []

	class FakeDockerRuntime:
		path_separator = ":"
		saved_image: str | None = None

		def prepare(self, _session: Any) -> None:
			events.append("prepare")

		def snapshot(self, _session: Any) -> str:
			self.saved_image = "aebench-oracle-snapshots:test"
			events.append("snapshot")
			return self.saved_image

		def stop(self, _session: Any) -> None:
			events.append("stop")

		def cleanup(self, _session: Any) -> None:
			events.append("cleanup")
			self.saved_image = None

		def runtime_result(self, _session: Any) -> RuntimeInfo:
			return RuntimeInfo(
				mode=RuntimeMode.DOCKER,
				image="aebench-agent:latest",
				workspace_mount="/repo",
				saved_image=self.saved_image,
				container_stopped="stop" in events,
			)

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
			if cmd[0] == "sh":
				return subprocess.CompletedProcess(cmd, 0, "", "")
			raise AssertionError("fake agent should not execute the runtime")

	runtime = FakeDockerRuntime()
	monkeypatch.setattr("runtime.case_runner.get_runtime", lambda *_args, **_kwargs: runtime)
	monkeypatch.setattr(
		"runtime.case_runner.prepare_agent_runtime",
		lambda _runtime: events.append("prepare_agent_runtime"),
	)

	def fake_agent(*_args: Any, model: str, **_kwargs: Any) -> AgentResult:
		events.append("agent")
		return AgentResult(model=model, exit_code=0)

	monkeypatch.setattr(
		"runtime.case_runner.run_agent",
		fake_agent,
	)

	class CapturingOracleRunner:
		def execute(self, _case_root: Path, **kwargs: Any) -> OracleResult:
			events.append("oracle")
			assert events[:6] == [
				"prepare",
				"prepare_agent_runtime",
				"agent",
				"stop",
				"snapshot",
				"oracle",
			]
			assert "runtime_session" not in kwargs
			assert "runtime_backend" not in kwargs
			assert kwargs["runtime_result"].runtime.saved_image == "aebench-oracle-snapshots:test"
			assert kwargs["runtime_result"].runtime.container_stopped is True
			return OracleResult(status=OracleStatus.SUCCESS, score=4)

	monkeypatch.setattr("runtime.case_runner.DirectOracleRunner", CapturingOracleRunner)
	result = run_case(
		context,
		project / "bundles" / "mock_apt_case",
		options=RunOptions(agent_type="codex", model_name="gpt-test"),
		save_path=tmp_path / "output",
	)

	assert result.status == CaseStatus.SUCCESS
	assert events == [
		"prepare",
		"prepare_agent_runtime",
		"agent",
		"stop",
		"snapshot",
		"oracle",
		"cleanup",
	]
	assert result.runtime_result.runtime.saved_image is None


def test_monitored_case_orders_the_broker_and_shim(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""Pins the ordering the monitoring layer depends on.

	The broker must bind before the container exists, or a shell can reach a
	socket that is not listening. The shim must be installed and proved before
	the agent runs, or a broken mount yields an empty trace and no error. The
	broker must stop before the container does, so no command is left in
	flight against a socket that is going away.
	"""
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	manifest = project / "bundles" / "mock_apt_case" / "case.toml"
	manifest.write_text(
		manifest.read_text(encoding="utf-8").replace(
			'mode = "local"\ntimeout_ms',
			'mode = "docker"\nimage = "aebench-agent:latest"\ntimeout_ms',
		),
		encoding="utf-8",
	)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))
	events: list[str] = []

	class FakeDockerRuntime:
		path_separator = ":"
		saved_image: str | None = None

		def prepare(self, _session: Any) -> None:
			events.append("container-start")

		def snapshot(self, _session: Any) -> str:
			self.saved_image = "aebench-oracle-snapshots:test"
			events.append("snapshot")
			return self.saved_image

		def stop(self, _session: Any) -> None:
			events.append("container-stop")

		def cleanup(self, _session: Any) -> None:
			events.append("cleanup")

		def runtime_result(self, _session: Any) -> RuntimeInfo:
			return RuntimeInfo(mode=RuntimeMode.DOCKER, saved_image=self.saved_image)

		def run_process(self, cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
			return subprocess.CompletedProcess(cmd, 0, "", "")

	class FakeBroker:
		def __init__(self, *, trace_dir: Path, workspace_dir: Path, socket_root: Path) -> None:
			self.trace_dir = trace_dir
			self.trace_path = trace_dir / "commands.jsonl"
			self.socket_dir = socket_root / "mon-test"
			self.workspace_dir = workspace_dir

		def start(self) -> None:
			events.append("broker-start")

		def stop(self) -> None:
			events.append("broker-stop")

		def command_count(self) -> int:
			return 7

	runtime = FakeDockerRuntime()
	monkeypatch.setattr("runtime.case_runner.get_runtime", lambda *_a, **_k: runtime)
	monkeypatch.setattr("runtime.case_runner.prepare_agent_runtime", lambda _r: None)
	monkeypatch.setattr("runtime.case_runner.BrokerProcess", FakeBroker)
	monkeypatch.setattr(
		"runtime.case_runner.install_shim", lambda _r: events.append("shim-install")
	)
	monkeypatch.setattr(
		"runtime.case_runner.verify_monitoring",
		lambda _runtime, _broker: events.append("shim-probe"),
	)

	captured: dict[str, Any] = {}

	def fake_agent(*_args: Any, model: str, shell_path: str, **_kwargs: Any) -> AgentResult:
		events.append("agent")
		captured["shell_path"] = shell_path
		return AgentResult(model=model, exit_code=0)

	monkeypatch.setattr("runtime.case_runner.run_agent", fake_agent)

	class StubOracleRunner:
		def execute(self, _case_root: Path, **_kwargs: Any) -> OracleResult:
			events.append("oracle")
			return OracleResult(status=OracleStatus.SUCCESS, score=4)

	monkeypatch.setattr("runtime.case_runner.DirectOracleRunner", StubOracleRunner)

	result = run_case(
		context,
		project / "bundles" / "mock_apt_case",
		options=RunOptions(agent_type="codex", model_name="gpt-test", monitor_commands=True),
		save_path=tmp_path / "output",
	)

	assert events == [
		"broker-start",
		"container-start",
		"shim-install",
		"shim-probe",
		"agent",
		"broker-stop",
		"container-stop",
		"snapshot",
		"oracle",
		"cleanup",
	]
	# AEBench's own shells bypass the shim; the agent's do not.
	assert captured["shell_path"] == "/usr/lib/aebench/bash.real"

	monitor = result.runtime_result.command_monitor
	assert monitor is not None
	assert monitor.command_count == 7
	# The trace lives in a per-case store keyed by a token, not in the run dir.
	trace_dir = Path(monitor.trace_path).parent
	assert trace_dir.parent.name == "monitor"
	assert trace_dir.name == monitor.run_token
	assert trace_dir != Path(result.runtime_result.output_dir)


def test_monitoring_requires_docker(tmp_path: Path) -> None:
	# The fixture case runs locally; monitoring it would mean replacing the
	# shell on the developer's own machine.
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))

	with pytest.raises(ValueError, match="requires runtime.mode = docker"):
		run_case(
			context,
			project / "bundles" / "mock_apt_case",
			options=RunOptions(
				agent_type="codex",
				model_name="gpt-test",
				monitor_commands=True,
				allow_unsafe_local=True,
			),
			save_path=tmp_path / "output",
		)
