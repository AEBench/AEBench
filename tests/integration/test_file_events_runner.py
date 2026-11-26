"""Tests that a monitored run records file events by default, next to the container,
and always stops the recorder, with fakes for the container, broker and recorder."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from config import AppState, resolve_settings
from models import (
	AgentResult,
	CaseStatus,
	FileEventsInfo,
	OracleResult,
	OracleStatus,
	RunOptions,
	RuntimeInfo,
	RuntimeMode,
)
from project_config import load_project_config
from runtime.case_runner import run_case

_FIXTURE = Path(__file__).parent / "mock-case" / "fixture"


@pytest.fixture(autouse=True)
def _mock_agent_key(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("MOCK_API_KEY", "test-key")


def _docker_context(tmp_path: Path) -> tuple[AppState, Path]:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	case_toml = project / "bundles" / "mock_apt_case" / "case.toml"
	case_toml.write_text(
		case_toml.read_text(encoding="utf-8").replace(
			'mode = "local"\ntimeout_ms',
			'mode = "docker"\nimage = "aebench-agent:latest"\ntimeout_ms',
		),
		encoding="utf-8",
	)
	state = load_project_config(project)
	return AppState(project_state=state, settings=resolve_settings(state)), (
		project / "bundles" / "mock_apt_case"
	)


class _Fakes:
	"""Patches the container, broker, shim and recorder, logging the order of calls."""

	def __init__(self, monkeypatch: pytest.MonkeyPatch, *, start_error: Exception | None = None):
		self.events: list[str] = []
		self.recorders: list[Any] = []
		events = self.events
		fakes = self

		class FakeDockerRuntime:
			path_separator = ":"
			saved_image: str | None = None
			container_id: str | None = None

			def prepare(self, _session: Any) -> None:
				self.container_id = "c0ffee"
				events.append("container-start")

			def snapshot(self, _session: Any) -> str:
				self.saved_image = "aebench-oracle-snapshots:test"
				return self.saved_image

			def stop(self, _session: Any) -> None:
				events.append("container-stop")

			def cleanup(self, _session: Any) -> None:
				events.append("cleanup")

			def runtime_result(self, _session: Any) -> RuntimeInfo:
				return RuntimeInfo(mode=RuntimeMode.DOCKER, saved_image=self.saved_image)

			def run_process(
				self, cmd: list[str], **_kwargs: Any
			) -> subprocess.CompletedProcess[str]:
				return subprocess.CompletedProcess(cmd, 0, "", "")

		class FakeBroker:
			def __init__(self, *, trace_dir: Path, socket_root: Path, **_kwargs: Any) -> None:
				self.trace_dir = trace_dir
				self.trace_path = trace_dir / "commands.jsonl"
				self.socket_dir = socket_root / "mon-test"
				self.stop_error = None

			def start(self) -> None:
				self.trace_dir.mkdir(parents=True, exist_ok=True)
				events.append("broker-start")

			def stop(self) -> None:
				events.append("broker-stop")

			def command_count(self) -> int:
				return 1

			def in_flight_count(self) -> int:
				return 0

			def workdir_snapshots_info(self) -> None:
				return None

		class FakeRecorder:
			def __init__(self, raw_path: Path, **_kwargs: Any) -> None:
				self.raw_path = raw_path
				fakes.recorders.append(self)

			def start(self, container: str) -> None:
				events.append(f"recorder-start:{container}")
				if start_error is not None:
					raise start_error

			def saw_exec(self, **_kwargs: Any) -> bool:
				return True

			def stop(self) -> None:
				events.append("recorder-stop")

			def link(self, commands_path: Path) -> FileEventsInfo:
				events.append(f"recorder-link:{commands_path.name}")
				return FileEventsInfo(
					status="recorded",
					raw_path=str(self.raw_path),
					path=str(self.raw_path.parent / "file_events.jsonl"),
					events=5,
				)

		class StubOracleRunner:
			def execute(self, _case_root: Path, **_kwargs: Any) -> OracleResult:
				events.append("oracle")
				return OracleResult(status=OracleStatus.SUCCESS, score=4)

		self.runtime = FakeDockerRuntime()
		monkeypatch.setattr("runtime.case_runner.get_runtime", lambda *_a, **_k: self.runtime)
		monkeypatch.setattr("runtime.case_runner.prepare_agent_runtime", lambda _r: None)
		monkeypatch.setattr("runtime.case_runner.BrokerProcess", FakeBroker)
		monkeypatch.setattr("runtime.case_runner.FileEventsRecorder", FakeRecorder)
		monkeypatch.setattr("runtime.case_runner.install_shim", lambda _r: None)
		monkeypatch.setattr("runtime.case_runner.check_system_files", lambda *_a: None)
		monkeypatch.setattr(
			"runtime.case_runner.verify_monitoring",
			lambda _runtime, _broker: events.append("shim-probe"),
		)
		monkeypatch.setattr("runtime.case_runner.DirectOracleRunner", StubOracleRunner)
		self.agent(monkeypatch)

	def agent(self, monkeypatch: pytest.MonkeyPatch, error: BaseException | None = None) -> None:
		events = self.events

		def fake_agent(*_args: Any, model: str, **_kwargs: Any) -> AgentResult:
			events.append("agent")
			if error is not None:
				raise error
			return AgentResult(model=model, exit_code=0)

		monkeypatch.setattr("runtime.case_runner.run_agent", fake_agent)

	def before(self, first: str, second: str) -> bool:
		return self.events.index(first) < self.events.index(second)


def _run(tmp_path: Path, **options: Any) -> Any:
	context, case_dir = _docker_context(tmp_path)
	return run_case(
		context,
		case_dir,
		options=RunOptions(
			agent_type="mock_agent", model_name="gpt-test", monitor_commands=True, **options
		),
		save_path=tmp_path / "output",
	)


def test_a_monitored_run_records_file_events_by_default(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	fakes = _Fakes(monkeypatch)

	result = _run(tmp_path)

	assert result.status == CaseStatus.SUCCESS
	# Started on the agent's container once it exists, before the probe shell runs.
	assert fakes.before("container-start", "recorder-start:c0ffee")
	assert fakes.before("recorder-start:c0ffee", "shim-probe")
	# Stopped and linked once the broker has written all of commands.jsonl.
	assert fakes.before("broker-stop", "recorder-stop")
	assert fakes.before("recorder-stop", "recorder-link:commands.jsonl")
	assert fakes.before("recorder-link:commands.jsonl", "container-stop")
	assert fakes.events.count("recorder-stop") == 1

	monitor = result.runtime_result.command_monitor
	assert monitor is not None and monitor.file_events is not None
	assert monitor.file_events.status == "recorded"
	assert monitor.file_events.events == 5
	raw = Path(fakes.recorders[0].raw_path)
	assert raw.name == "file_events.raw.tsv"
	assert raw.parent == Path(monitor.trace_path).parent
	report = (tmp_path / "output").glob("*report*.md")
	assert any("## File Events" in path.read_text(encoding="utf-8") for path in report)


def test_a_recorder_that_cannot_start_leaves_the_run_unharmed(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	fakes = _Fakes(monkeypatch, start_error=RuntimeError("privileged containers are not allowed"))

	result = _run(tmp_path)

	assert result.status == CaseStatus.SUCCESS
	assert result.runtime_result.error is None
	info = result.runtime_result.command_monitor.file_events
	assert info is not None and info.status == "unavailable"
	assert "privileged" in (info.reason or "")
	assert "agent" in fakes.events
	# A half-started recorder is still stopped, and nothing is linked.
	assert "recorder-stop" in fakes.events
	assert not any(e.startswith("recorder-link") for e in fakes.events)


def test_the_recorder_is_stopped_when_setup_fails(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	fakes = _Fakes(monkeypatch)

	def broken_probe(_runtime: Any, _broker: Any) -> None:
		raise RuntimeError("the shim never reached the broker")

	monkeypatch.setattr("runtime.case_runner.verify_monitoring", broken_probe)

	result = _run(tmp_path)

	assert result.status == CaseStatus.ERROR
	assert "agent" not in fakes.events
	assert fakes.before("broker-stop", "recorder-stop")
	assert fakes.before("recorder-stop", "recorder-link:commands.jsonl")


def test_the_recorder_is_stopped_when_the_run_is_interrupted(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	fakes = _Fakes(monkeypatch)
	fakes.agent(monkeypatch, error=KeyboardInterrupt())

	with pytest.raises(KeyboardInterrupt):
		_run(tmp_path)

	assert fakes.events.count("recorder-stop") == 1
	assert fakes.before("broker-stop", "recorder-stop")


def test_no_file_events_never_starts_the_recorder(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	fakes = _Fakes(monkeypatch)

	result = _run(tmp_path, file_events=False)

	assert result.status == CaseStatus.SUCCESS
	assert fakes.recorders == []
	info = result.runtime_result.command_monitor.file_events
	assert info is not None and info.status == "disabled"
