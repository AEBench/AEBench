"""Result aggregation tests: pass ratio, phase score, markdown, output files."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from artevalbench.models import (
	AgentResult,
	CasePlan,
	CaseRunResult,
	CaseStatus,
	OracleResult,
	OracleStatus,
	PromptProfile,
	RuntimeMode,
	RuntimeInfo,
	RunResult,
	TaskStatus,
)
from artevalbench.runtime.benchmark_runner import (
	_summarize,
	render_benchmark_summary_markdown,
	write_benchmark_outputs,
)


def _now() -> datetime:
	return datetime.now(timezone.utc)


def _run_result(id: str) -> RunResult:
	now = _now()
	return RunResult(
		id=id,
		status=TaskStatus.SUCCESS,
		started_at=now,
		finished_at=now,
		duration_ms=0,
		workspace_path="/tmp/workspace",
		output_dir="/tmp/output",
		summary_path="/tmp/summary.txt",
		prompt_profile=PromptProfile.ARTIFACT_EVAL_V1,
		runtime=RuntimeInfo(mode=RuntimeMode.LOCAL),
		agent_kind="mock",
		agent=AgentResult(model="test-model", exit_code=0),
	)


def _case_result(
	id: str,
	*,
	case_status: CaseStatus = CaseStatus.SUCCESS,
	oracle_score: int = 4,
	oracle_status: OracleStatus = OracleStatus.SUCCESS,
) -> CaseRunResult:
	return CaseRunResult(
		status=case_status,
		finished_at=_now(),
		case_dir="/tmp/case",
		artifact_dir="/tmp/artifact",
		output_dir="/tmp/output",
		case_brief=CasePlan(
			core_claim="Test claim for " + id,
			acceptable_evidence="Evidence.",
			allowed_tolerance="None.",
		),
		runtime_result=_run_result(id),
		oracle_result=OracleResult(status=oracle_status, score=oracle_score),
	)


def _summarize_results(case_results, expected_scores=None):
	if expected_scores is None:
		expected_scores = {r.id: 4 for r in case_results}
	now = _now()
	return _summarize(
		case_results,
		[r.id for r in case_results],
		now,
		now,
		run_label="test-run",
		model_name="test-model",
		agent_kind="mock",
		prompt_profile="artifact-eval-v1",
		runtime_mode="local",
		expected_scores=expected_scores,
		interrupted=False,
	)


def test_all_cases_pass_ratio_one() -> None:
	results = [_case_result(f"case_{i}") for i in range(3)]
	summary = _summarize_results(results)
	assert summary.case_pass_ratio == 1.0
	assert summary.case_pass_count == 3


def test_all_cases_pass_total_score() -> None:
	results = [_case_result(f"case_{i}", oracle_score=4) for i in range(3)]
	summary = _summarize_results(results, {r.id: 4 for r in results})
	assert summary.total_score == 12
	assert summary.total_expected_score == 12


def test_partial_pass_ratio() -> None:
	results = [
		_case_result("a", case_status=CaseStatus.SUCCESS, oracle_score=4),
		_case_result("b", case_status=CaseStatus.ERROR, oracle_score=2, oracle_status=OracleStatus.ERROR),
		_case_result("c", case_status=CaseStatus.ERROR, oracle_score=0, oracle_status=OracleStatus.ERROR),
	]
	summary = _summarize_results(results)
	assert summary.case_pass_count == 1
	assert abs(summary.case_pass_ratio - 1 / 3) < 1e-9


def test_partial_pass_total_score() -> None:
	results = [
		_case_result("a", oracle_score=4),
		_case_result("b", oracle_score=2, case_status=CaseStatus.ERROR, oracle_status=OracleStatus.ERROR),
		_case_result("c", oracle_score=0, case_status=CaseStatus.ERROR, oracle_status=OracleStatus.ERROR),
	]
	summary = _summarize_results(results)
	assert summary.total_score == 6


def test_empty_case_list_zero_ratio() -> None:
	summary = _summarize_results([])
	assert summary.case_pass_ratio == 0.0
	assert summary.total_cases == 0
	assert summary.total_score == 0


def test_phase_ratio_computed_correctly() -> None:
	results = [_case_result("a", oracle_score=2), _case_result("b", oracle_score=4)]
	summary = _summarize_results(results, {"a": 4, "b": 4})
	# total_score=6, total_expected=8 → 0.75
	assert abs(summary.phase_ratio - 0.75) < 1e-9


def test_status_all_pass_is_success() -> None:
	results = [_case_result("a"), _case_result("b")]
	summary = _summarize_results(results)
	assert summary.status == "success"


def test_status_partial_pass_is_error() -> None:
	results = [
		_case_result("a"),
		_case_result("b", case_status=CaseStatus.ERROR, oracle_status=OracleStatus.ERROR),
	]
	summary = _summarize_results(results)
	assert summary.status == "error"


def test_markdown_contains_all_case_ids() -> None:
	results = [_case_result("egwalker_case"), _case_result("wasabi_case")]
	summary = _summarize_results(results, {r.id: 4 for r in results})
	md = render_benchmark_summary_markdown(summary, results, expected_scores={r.id: 4 for r in results})
	assert "egwalker_case" in md
	assert "wasabi_case" in md


def test_markdown_contains_score_column() -> None:
	results = [_case_result("mycase", oracle_score=3)]
	summary = _summarize_results(results, {"mycase": 4})
	md = render_benchmark_summary_markdown(summary, results, expected_scores={"mycase": 4})
	assert "3/4" in md


def test_markdown_contains_summary_header() -> None:
	results = [_case_result("a")]
	summary = _summarize_results(results)
	md = render_benchmark_summary_markdown(summary, results, expected_scores={"a": 4})
	assert "# Benchmark Summary" in md


def test_markdown_failures_section_present_when_failures_exist() -> None:
	results = [
		_case_result("ok"),
		_case_result("fail", case_status=CaseStatus.ERROR, oracle_status=OracleStatus.ERROR),
	]
	summary = _summarize_results(results)
	md = render_benchmark_summary_markdown(summary, results, expected_scores={r.id: 4 for r in results})
	assert "## Failures" in md


def test_markdown_failures_section_absent_when_all_pass() -> None:
	results = [_case_result("a"), _case_result("b")]
	summary = _summarize_results(results)
	md = render_benchmark_summary_markdown(summary, results, expected_scores={r.id: 4 for r in results})
	assert "## Failures" not in md


def test_write_benchmark_outputs_creates_files(tmp_path: Path) -> None:
	results = [_case_result("a"), _case_result("b")]
	summary = _summarize_results(results, {r.id: 4 for r in results})
	results_path, summary_path, md_path = write_benchmark_outputs(
		tmp_path, results, summary, expected_scores={r.id: 4 for r in results}
	)
	assert Path(results_path).is_file()
	assert Path(summary_path).is_file()
	assert Path(md_path).is_file()


def test_benchmark_results_jsonl_has_one_line_per_case(tmp_path: Path) -> None:
	results = [_case_result(f"case_{i}") for i in range(3)]
	summary = _summarize_results(results)
	results_path, _, _ = write_benchmark_outputs(
		tmp_path, results, summary, expected_scores={r.id: 4 for r in results}
	)
	lines = [l for l in Path(results_path).read_text().splitlines() if l.strip()]
	assert len(lines) == 3
