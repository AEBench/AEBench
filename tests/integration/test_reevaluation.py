from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from models import (
	AgentResult,
	CaseConfig,
	CasePlan,
	OracleConfig,
	OracleFailureMode,
	OracleStatus,
	PaperConfig,
	PromptProfile,
	RunResult,
	RuntimeConfig,
	RuntimeInfo,
	RuntimeMode,
	TaskConfig,
	TaskStatus,
)
from runtime.reevaluation import load_completed_run, reevaluate_completed_run

_ORACLES = textwrap.dedent("""\
	from evaluator.oracles.bases import (
		CaseOracleArtifactBuildBase,
		CaseOracleBenchmarkPrepBase,
		CaseOracleEnvSetupBase,
		CaseOracleExperimentRunsBase,
	)

	class OracleEnvSetup(CaseOracleEnvSetupBase):
		def requirements(self):
			return []

	class OracleArtifactBuild(CaseOracleArtifactBuildBase):
		def requirements(self):
			return []

	class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
		def requirements(self):
			return []

	class OracleExperimentRuns(CaseOracleExperimentRunsBase):
		def requirements(self):
			return []
""")


def _case_spec(case_id: str = "fixture_case") -> CaseConfig:
	return CaseConfig(
		id=case_id,
		case_brief=CasePlan(
			core_claim="Integration test fixture.",
			acceptable_evidence="All checks pass.",
			allowed_tolerance="None.",
		),
		run=TaskConfig(id=case_id, runtime=RuntimeConfig(mode=RuntimeMode.LOCAL)),
		paper=PaperConfig(
			url="https://example.com/paper.pdf",
			sha256="2717c4619708f534915e6b567feaa6a1001e1a5f782268e47e7dabdefb380de4",
			title="Example Paper",
		),
		oracle=OracleConfig(expected_score=4, failure_mode=OracleFailureMode.CONTINUE),
	)


def _run_result(
	workspace: Path,
	*,
	case_id: str = "fixture_case",
	mode: RuntimeMode = RuntimeMode.LOCAL,
	saved_image: str | None = None,
) -> RunResult:
	now = datetime.now(timezone.utc)
	return RunResult(
		id=case_id,
		status=TaskStatus.SUCCESS,
		started_at=now,
		finished_at=now,
		duration_ms=1,
		workspace_path=str(workspace),
		output_dir="/tmp/output",
		summary_path="/tmp/summary.md",
		prompt_profile=PromptProfile.ARTIFACT_EVAL_V1,
		runtime=RuntimeInfo(mode=mode, saved_image=saved_image),
		agent_kind="mock",
		agent=AgentResult(model="test-model", exit_code=0),
	)


def _write_completed_run(run_dir: Path, result: RunResult) -> None:
	run_dir.mkdir()
	(run_dir / "result.jsonl").write_text(result.model_dump_json() + "\n", encoding="utf-8")


def test_load_completed_run_rejects_case_mismatch(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	run_dir = tmp_path / "run"
	_write_completed_run(run_dir, _run_result(workspace, case_id="different_case"))

	with pytest.raises(ValueError, match="run case mismatch"):
		load_completed_run(run_dir, expected_case_id="fixture_case")


def test_load_completed_run_requires_preserved_docker_snapshot(tmp_path: Path) -> None:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	run_dir = tmp_path / "run"
	_write_completed_run(run_dir, _run_result(workspace, mode=RuntimeMode.DOCKER))

	with pytest.raises(ValueError, match="no preserved runtime snapshot"):
		load_completed_run(run_dir, expected_case_id="fixture_case")


def test_load_completed_run_requires_recorded_workspace(tmp_path: Path) -> None:
	run_dir = tmp_path / "run"
	_write_completed_run(run_dir, _run_result(tmp_path / "missing-workspace"))

	with pytest.raises(ValueError, match="recorded workspace not found"):
		load_completed_run(run_dir, expected_case_id="fixture_case")


@pytest.mark.sanity
def test_reevaluate_completed_run_preserves_original_results(tmp_path: Path) -> None:
	case_dir = tmp_path / "fixture_case"
	oracles_dir = case_dir / "oracles"
	oracles_dir.mkdir(parents=True)
	(oracles_dir / "all_phases.py").write_text(_ORACLES, encoding="utf-8")
	(case_dir / "refs").mkdir()

	workspace = tmp_path / "workspace"
	workspace.mkdir()
	run_dir = tmp_path / "run"
	_write_completed_run(run_dir, _run_result(workspace))
	original_result = run_dir / "oracle_result.json"
	original_result.write_text('{"score": 2}\n', encoding="utf-8")

	record, evaluation_dir = reevaluate_completed_run(
		case_dir=case_dir,
		case=_case_spec(),
		run_dir=run_dir,
		project_root=tmp_path,
	)

	assert record.oracle_result.status == OracleStatus.SUCCESS
	assert record.oracle_result.score == 4
	assert record.source_revision is None
	assert record.runtime_snapshot is None
	assert (evaluation_dir / "oracle_result.json").is_file()
	assert (evaluation_dir / "evaluation.json").is_file()
	assert original_result.read_text(encoding="utf-8") == '{"score": 2}\n'
