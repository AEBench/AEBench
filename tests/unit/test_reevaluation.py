from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from models import (
	AgentResult,
	CasePlan,
	CaseRunResult,
	CaseStatus,
	OracleResult,
	OracleStatus,
	PromptProfile,
	RunResult,
	RuntimeInfo,
	RuntimeMode,
	TaskStatus,
)
from runtime import reevaluation


def _docker_run_result(workspace: Path, *, saved_image: str) -> RunResult:
	now = datetime.now(timezone.utc)
	return RunResult(
		id="fixture_case",
		status=TaskStatus.SUCCESS,
		started_at=now,
		finished_at=now,
		duration_ms=1,
		workspace_path=str(workspace),
		output_dir="/tmp/output",
		summary_path="/tmp/summary.md",
		prompt_profile=PromptProfile.ARTIFACT_EVAL_V1,
		runtime=RuntimeInfo(mode=RuntimeMode.DOCKER, saved_image=saved_image),
		agent_kind="mock",
		agent=AgentResult(model="test-model", exit_code=0),
	)


def test_load_completed_run_accepts_available_docker_snapshot(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	run_dir = tmp_path / "run"
	run_dir.mkdir()
	result = _docker_run_result(workspace, saved_image="aebench-snapshot:test")
	(run_dir / "result.jsonl").write_text(result.model_dump_json() + "\n", encoding="utf-8")
	monkeypatch.setattr(reevaluation.shutil, "which", lambda _name: "/usr/bin/docker")
	monkeypatch.setattr(
		reevaluation.subprocess,
		"run",
		lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
	)

	completed = reevaluation.load_completed_run(run_dir, expected_case_id="fixture_case")

	assert completed.runtime_result.runtime.saved_image == "aebench-snapshot:test"
	assert completed.workspace_dir == workspace


def test_load_completed_run_rejects_missing_docker_image(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	run_dir = tmp_path / "run"
	run_dir.mkdir()
	result = _docker_run_result(workspace, saved_image="aebench-snapshot:missing")
	(run_dir / "result.jsonl").write_text(result.model_dump_json() + "\n", encoding="utf-8")
	monkeypatch.setattr(reevaluation.shutil, "which", lambda _name: "/usr/bin/docker")
	monkeypatch.setattr(
		reevaluation.subprocess,
		"run",
		lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "not found"),
	)

	with pytest.raises(ValueError, match="recorded runtime snapshot not found"):
		reevaluation.load_completed_run(run_dir, expected_case_id="fixture_case")


def test_read_run_result_accepts_case_result(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	runtime_result = _docker_run_result(workspace, saved_image="aebench-snapshot:test")
	case_result = CaseRunResult(
		status=CaseStatus.SUCCESS,
		finished_at=datetime.now(timezone.utc),
		case_dir="/tmp/case",
		artifact_dir=str(workspace),
		output_dir=str(tmp_path),
		case_brief=CasePlan(
			core_claim="Fixture claim.",
			acceptable_evidence="Fixture evidence.",
			allowed_tolerance="None.",
		),
		runtime_result=runtime_result,
		oracle_result=OracleResult(status=OracleStatus.SUCCESS, score=4),
	)
	(tmp_path / "case_result.json").write_text(
		case_result.model_dump_json(),
		encoding="utf-8",
	)

	assert reevaluation.read_run_result(tmp_path) == runtime_result


def test_read_run_result_accepts_recorded_workspace_mount(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	runtime_result = _docker_run_result(workspace, saved_image="aebench-snapshot:test")
	payload = runtime_result.model_dump(mode="json")
	payload["runtime"]["workspace_mount"] = "/repo"
	(tmp_path / "result.jsonl").write_text(
		RunResult.model_validate(payload).model_dump_json() + "\n",
		encoding="utf-8",
	)

	result = reevaluation.read_run_result(tmp_path)

	assert result is not None
	assert result.runtime.workspace_mount == "/repo"
