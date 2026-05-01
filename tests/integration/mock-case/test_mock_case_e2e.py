"""End-to-end mock artifact case test."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from config import AppState, resolve_settings
from models import CaseStatus, OracleStatus, RunOptions, TaskStatus
from project_config import load_project_config
from runtime.case_runner import CaseRunner
from runtime.cases import resolve_case_dir

_FIXTURE_DIR = Path(__file__).parent / "fixture"
_CASE_ID = "mock_apt_case"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    dest = tmp_path / "workspace"
    shutil.copytree(_FIXTURE_DIR, dest)
    return dest


@pytest.fixture()
def ctx(workspace: Path) -> AppState:
    state = load_project_config(workspace)
    settings = resolve_settings(state)
    return AppState(project_state=state, settings=settings)


@pytest.mark.sanity
def test_case_run_end_to_end(workspace: Path, ctx: AppState, tmp_path: Path) -> None:
    case_dir = resolve_case_dir(_CASE_ID, project_state=ctx.project_state)
    output_dir = tmp_path / "case_output"

    result = CaseRunner(ctx).run(
        case_dir,
        save_path=output_dir,
        options=RunOptions(),
    )

    assert result.status == CaseStatus.SUCCESS
    assert result.runtime_result.status == TaskStatus.SUCCESS
    assert result.runtime_result.agent.exit_code == 0

    assert result.oracle_result.status == OracleStatus.SUCCESS
    assert result.oracle_result.score == 4
    assert len(result.oracle_result.phases) == 4
    assert all(phase.status == OracleStatus.SUCCESS for phase in result.oracle_result.phases)

    case_result_path = output_dir / "case_result.json"
    assert case_result_path.is_file()
    payload = json.loads(case_result_path.read_text(encoding="utf-8"))
    assert payload["runtime_result"]["id"] == _CASE_ID

    assert (output_dir / "oracle_result.json").is_file()
    assert list(output_dir.glob("*_report.md"))
    assert list(output_dir.glob("*.log"))