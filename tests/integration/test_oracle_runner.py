"""Oracle runtime infrastructure (***OracleRunner) integration tests."""
from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from models import (
	AgentResult,
	CasePlan,
	CaseConfig,
	OracleConfig,
	OracleStatus,
	PromptProfile,
	RunResult,
	TaskConfig,
	RuntimeMode,
	RuntimeInfo,
	RuntimeConfig,
	TaskStatus,
)
from runtime.oracle_runner import DirectOracleRunner, SubprocessOracleRunner


_FIXTURE_ORACLE = textwrap.dedent("""\
	from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
	from evaluator.oracles.env_setup_checks import FilesystemPathCheck

	class OracleArtifactBuild(CaseOracleArtifactBuildBase):
		def requirements(self):
			built_txt = self.paths.workspace_dir / "built.txt"
			return [FilesystemPathCheck(name="built_txt", path=built_txt)]
""")


def _make_case_spec(id: str = "fixture_case") -> CaseConfig:
	return CaseConfig(
		id=id,
		case_brief=CasePlan(
			core_claim="Integration test fixture.",
			acceptable_evidence="built.txt exists in workspace.",
			allowed_tolerance="None.",
		),
		run=TaskConfig(id=id, runtime=RuntimeConfig(mode=RuntimeMode.LOCAL)),
		oracle=OracleConfig(phases=["artifact_build"]),
	)


def _make_run_result(id: str, workspace_path: str) -> RunResult:
	now = datetime.now(timezone.utc)
	return RunResult(
		id=id,
		status=TaskStatus.SUCCESS,
		started_at=now,
		finished_at=now,
		duration_ms=0,
		workspace_path=workspace_path,
		output_dir="/tmp",
		summary_path="/tmp/summary.txt",
		prompt_profile=PromptProfile.ARTIFACT_EVAL_V1,
		runtime=RuntimeInfo(mode=RuntimeMode.LOCAL),
		agent_kind="mock",
		agent=AgentResult(model="test-model", exit_code=0),
	)


@pytest.fixture()
def fixture_case_dir(tmp_path: Path) -> Path:
	case_dir = tmp_path / "fixture_case"
	case_dir.mkdir()
	(case_dir / "refs").mkdir()
	oracle_dir = case_dir / "oracles"
	oracle_dir.mkdir()
	(oracle_dir / "artifact_build.py").write_text(_FIXTURE_ORACLE, encoding="utf-8")
	return case_dir


@pytest.fixture()
def valid_workspace(tmp_path: Path) -> Path:
	workspace = tmp_path / "workspace"
	workspace.mkdir()
	(workspace / "built.txt").write_text("build output\n", encoding="utf-8")
	return workspace


@pytest.fixture()
def empty_workspace(tmp_path: Path) -> Path:
	workspace = tmp_path / "empty_workspace"
	workspace.mkdir()
	return workspace


@pytest.mark.sanity
def test_direct_oracle_runner_passes_on_valid_workspace(
	fixture_case_dir: Path, valid_workspace: Path, tmp_path: Path
) -> None:
	output_dir = tmp_path / "output_direct_pass"
	spec = _make_case_spec()
	runtime_result = _make_run_result("fixture_case", str(valid_workspace))

	result = DirectOracleRunner().execute(
		fixture_case_dir,
		runtime_result=runtime_result,
		output_dir=output_dir,
		case=spec,
	)

	assert result.status == OracleStatus.SUCCESS
	assert result.score == 1


@pytest.mark.sanity
def test_direct_oracle_runner_fails_on_empty_workspace(
	fixture_case_dir: Path, empty_workspace: Path, tmp_path: Path
) -> None:
	output_dir = tmp_path / "output_direct_fail"
	spec = _make_case_spec()
	runtime_result = _make_run_result("fixture_case", str(empty_workspace))

	result = DirectOracleRunner().execute(
		fixture_case_dir,
		runtime_result=runtime_result,
		output_dir=output_dir,
		case=spec,
	)

	assert result.status == OracleStatus.ERROR
	assert result.score == 0


@pytest.mark.sanity
def test_oracle_result_written_to_disk(
	fixture_case_dir: Path, valid_workspace: Path, tmp_path: Path
) -> None:
	output_dir = tmp_path / "output_disk_check"
	spec = _make_case_spec()
	runtime_result = _make_run_result("fixture_case", str(valid_workspace))

	DirectOracleRunner().execute(
		fixture_case_dir,
		runtime_result=runtime_result,
		output_dir=output_dir,
		case=spec,
	)

	assert (output_dir / "oracle_result.json").is_file()


@pytest.mark.sanity
def test_direct_runner_phase_list_populated(
	fixture_case_dir: Path, valid_workspace: Path, tmp_path: Path
) -> None:
	output_dir = tmp_path / "output_phases"
	spec = _make_case_spec()
	runtime_result = _make_run_result("fixture_case", str(valid_workspace))

	result = DirectOracleRunner().execute(
		fixture_case_dir,
		runtime_result=runtime_result,
		output_dir=output_dir,
		case=spec,
	)

	assert len(result.phases) == 1
	assert result.phases[0].phase == "artifact_build"


@pytest.fixture()
def fixture_case_dir_with_toml(fixture_case_dir: Path) -> Path:
	toml_content = textwrap.dedent("""\
		id = "fixture_case"

		[case_brief]
		core_claim = "Integration test fixture."
		acceptable_evidence = "built.txt exists in workspace."
		allowed_tolerance = "None."

		[run]
		id = "fixture_case"
		[run.runtime]
		mode = "local"

		[oracle]
		phases = ["artifact_build"]
	""")
	(fixture_case_dir / "case.toml").write_text(toml_content, encoding="utf-8")
	(fixture_case_dir / "artifact").mkdir(exist_ok=True)
	(fixture_case_dir / "artifact" / "README.md").write_text("# fixture\n", encoding="utf-8")
	return fixture_case_dir


@pytest.mark.sanity
def test_subprocess_oracle_runner_matches_direct(
	fixture_case_dir_with_toml: Path, valid_workspace: Path, tmp_path: Path
) -> None:
	runtime_result = _make_run_result("fixture_case", str(valid_workspace))
	spec = _make_case_spec()

	direct_result = DirectOracleRunner().execute(
		fixture_case_dir_with_toml,
		runtime_result=runtime_result,
		output_dir=tmp_path / "out_direct",
		case=spec,
	)
	subprocess_result = SubprocessOracleRunner().execute(
		fixture_case_dir_with_toml,
		runtime_result=runtime_result,
		output_dir=tmp_path / "out_subprocess",
		# case= intentionally omitted; subprocess loads it from case.toml
	)

	assert subprocess_result.status == direct_result.status
	assert subprocess_result.score == direct_result.score
