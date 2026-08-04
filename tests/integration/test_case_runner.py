from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
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

	def fake_agent(*_args: Any, output_path: Path, model: str, **_kwargs: Any) -> AgentResult:
		output_path.write_text('{"type":"result"}\n', encoding="utf-8")
		return AgentResult(model=model, exit_code=0)

	monkeypatch.setattr("runtime.case_runner.run_agent", fake_agent)
	result = run_case(
		context,
		project / "bundles" / "mock_apt_case",
		options=RunOptions(agent_type="codex", model_name="gpt-test", allow_unsafe_local=True),
		save_path=output_dir,
	)

	assert result.status == CaseStatus.SUCCESS
	assert result.oracle_result.score == 4
	assert result.runtime_result.agent_kind == "codex"
	assert (output_dir / "runner_output.log").is_file()
	prompt = next(output_dir.glob("aebench_prompt_*.md")).read_text(encoding="utf-8")
	assert "Acceptable Evidence" in prompt
	assert "Allowed Tolerance" in prompt
	assert (output_dir / "result.jsonl").is_file()
	assert (output_dir / "case_result.json").is_file()


def test_case_runner_rejects_unisolated_local_agent(tmp_path: Path) -> None:
	project = tmp_path / "project"
	shutil.copytree(_FIXTURE, project)
	state = load_project_config(project)
	context = AppState(project_state=state, settings=resolve_settings(state))

	with pytest.raises(ValueError, match="not isolated"):
		run_case(
			context,
			project / "bundles" / "mock_apt_case",
			options=RunOptions(agent_type="codex", model_name="gpt-test"),
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

	assert result.status == CaseStatus.SUCCESS
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
		"runtime.case_runner.run_agent",
		lambda *_args, model, **_kwargs: AgentResult(model=model, exit_code=0),
	)

	class CapturingOracleRunner:
		def execute(self, _case_root: Path, **kwargs: Any) -> OracleResult:
			events.append("oracle")
			assert events[:4] == ["prepare", "stop", "snapshot", "oracle"]
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
	assert events == ["prepare", "stop", "snapshot", "oracle", "cleanup"]
	assert result.runtime_result.runtime.saved_image is None
