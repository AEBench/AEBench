"""End-to-end mock artifact (case) test"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from config import AppState, resolve_settings
from models import CaseStatus, OracleStatus, RunOptions, TaskStatus
from project_config import load_project_config
from runtime.benchmark_runner import BenchmarkRunner
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
    """Build AppState from fixture workspace, same as CLI does."""
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

    assert result.status == CaseStatus.SUCCESS, (
        f"expected CaseStatus.SUCCESS, got {result.status}; "
        f"runtime_error={result.runtime_result.error!r}"
    )

    assert result.runtime_result.status == TaskStatus.SUCCESS
    assert result.runtime_result.agent.exit_code == 0

    assert result.oracle_result.status == OracleStatus.SUCCESS
    assert result.oracle_result.score == 4
    assert len(result.oracle_result.phases) == 4
    for phase in result.oracle_result.phases:
        assert phase.status == OracleStatus.SUCCESS, (
            f"phase {phase.phase!r} did not pass: {phase.summary!r}"
        )

    case_result_path = output_dir / "case_result.json"
    assert case_result_path.is_file(), "case_result.json was not written"
    payload = json.loads(case_result_path.read_text(encoding="utf-8"))
    assert payload["runtime_result"]["id"] == _CASE_ID

    assert (output_dir / "oracle_result.json").is_file(), "oracle_result.json was not written"

    report_files = list(output_dir.glob("aebench_report_*.md"))
    assert report_files, "no task report Markdown file was written under output_dir"

    log_files = list(output_dir.glob("aebench_log_*.log"))
    assert log_files, "no log file was written under output_dir"


@pytest.mark.sanity
def test_benchmark_run_end_to_end(workspace: Path, ctx: AppState, tmp_path: Path) -> None:
    output_dir = tmp_path / "benchmark_output"

    result = BenchmarkRunner(ctx).run(
        [_CASE_ID],
        options=RunOptions(),
        output_dir=output_dir,
    )

    assert result.summary.status == "success"
    assert result.summary.total_cases == 1
    assert result.summary.case_pass_count == 1
    assert result.summary.total_score == 4
    assert result.summary.total_expected_score == 4
    assert abs(result.summary.case_pass_ratio - 1.0) < 1e-9
    assert abs(result.summary.phase_ratio - 1.0) < 1e-9

    assert len(result.case_results) == 1
    case_result = result.case_results[0]
    assert case_result.status == CaseStatus.SUCCESS

    assert Path(result.case_results_path).is_file(), "benchmark_results.jsonl was not written"
    assert Path(result.summary_path).is_file(), "benchmark_summary.json was not written"
    assert Path(result.summary_markdown_path).is_file(), "benchmark_summary.md was not written"

    lines = [
        l for l in Path(result.case_results_path).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(lines) == 1

    md = Path(result.summary_markdown_path).read_text(encoding="utf-8")
    assert _CASE_ID in md
    assert "success" in md.lower()
