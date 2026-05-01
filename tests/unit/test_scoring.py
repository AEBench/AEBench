"""Oracle scoring and failure-mode tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from evaluator.oracles.execution import run_oracle
from models import OracleFailureMode, OracleStatus


CASE_TOML = """\
id = "fixture_case"

[case_brief]
core_claim = "Scoring fixture."
acceptable_evidence = "Oracle result matches expected score."
allowed_tolerance = "None."

[paper]
url = "https://example.com/paper.pdf"
sha256 = "2717c4619708f534915e7b567feaa6a1001e1a5f782268e47e7dabdefb380de4"
title = "Example Paper"

[run]
id = "fixture_case"

[run.instructions]
path = "README.md"

[run.runtime]
mode = "local"

[oracle]
expected_score = 4
failure_mode = "{failure_mode}"
placeholder = false
"""


ORACLE_CODE = """\
from evaluator.oracles.bases import (
    CaseOracleEnvSetupBase,
    CaseOracleArtifactBuildBase,
    CaseOracleBenchmarkPrepBase,
    CaseOracleExperimentRunsBase,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self):
        return {env_setup}


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self):
        return {artifact_build}


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self):
        return {benchmark_prep}


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self):
        return {experiment_runs}
"""


FAIL_CHECK = """[
    self.path_check(
        name="missing_file",
        path=self.workspace_path("missing.txt"),
        kind="file",
    )
]"""


def make_case(
    tmp_path: Path,
    *,
    failure_mode: OracleFailureMode = OracleFailureMode.FAIL_FAST,
    env_setup: str = "[]",
    artifact_build: str = "[]",
    benchmark_prep: str = "[]",
    experiment_runs: str = "[]",
) -> Path:
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    (case_dir / "case.toml").write_text(
        CASE_TOML.format(failure_mode=failure_mode.value),
        encoding="utf-8",
    )

    artifact_dir = case_dir / "artifact"
    artifact_dir.mkdir()
    (artifact_dir / "README.md").write_text("# fixture\n", encoding="utf-8")

    (case_dir / "refs").mkdir()

    oracle_dir = case_dir / "oracles"
    oracle_dir.mkdir()
    (oracle_dir / "__init__.py").write_text("", encoding="utf-8")
    (oracle_dir / "oracle.py").write_text(
        textwrap.dedent(
            ORACLE_CODE.format(
                env_setup=env_setup,
                artifact_build=artifact_build,
                benchmark_prep=benchmark_prep,
                experiment_runs=experiment_runs,
            )
        ),
        encoding="utf-8",
    )

    return case_dir


def run_case(case_dir: Path, tmp_path: Path):
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    workspace.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)

    return run_oracle(
        case_dir,
        runtime_result=None,
        output_dir=output,
        workspace_dir=workspace,
    )


def test_score_all_oracles_pass(tmp_path: Path) -> None:
    result = run_case(make_case(tmp_path), tmp_path)

    assert result.status == OracleStatus.SUCCESS
    assert result.score == 4


def test_score_partial_oracles_pass_in_continue_mode(tmp_path: Path) -> None:
    case_dir = make_case(
        tmp_path,
        failure_mode=OracleFailureMode.CONTINUE,
        artifact_build=FAIL_CHECK,
    )

    result = run_case(case_dir, tmp_path)

    assert result.status == OracleStatus.ERROR
    assert result.score == 3


def test_score_zero_when_all_oracles_fail_in_continue_mode(tmp_path: Path) -> None:
    case_dir = make_case(
        tmp_path,
        failure_mode=OracleFailureMode.CONTINUE,
        env_setup=FAIL_CHECK,
        artifact_build=FAIL_CHECK,
        benchmark_prep=FAIL_CHECK,
        experiment_runs=FAIL_CHECK,
    )

    result = run_case(case_dir, tmp_path)

    assert result.status == OracleStatus.ERROR
    assert result.score == 0


def test_fail_fast_marks_remaining_oracles_pending(tmp_path: Path) -> None:
    case_dir = make_case(tmp_path, env_setup=FAIL_CHECK)

    result = run_case(case_dir, tmp_path)

    assert result.score == 0
    assert [entry.status for entry in result.phases] == [
        OracleStatus.ERROR,
        OracleStatus.PENDING,
        OracleStatus.PENDING,
        OracleStatus.PENDING,
    ]


def test_continue_mode_runs_all_oracles(tmp_path: Path) -> None:
    case_dir = make_case(
        tmp_path,
        failure_mode=OracleFailureMode.CONTINUE,
        env_setup=FAIL_CHECK,
    )

    result = run_case(case_dir, tmp_path)

    assert result.score == 3
    assert [entry.status for entry in result.phases] == [
        OracleStatus.ERROR,
        OracleStatus.SUCCESS,
        OracleStatus.SUCCESS,
        OracleStatus.SUCCESS,
    ]


def test_requirements_exception_is_captured(tmp_path: Path) -> None:
    case_dir = make_case(tmp_path, env_setup='(_ for _ in ()).throw(RuntimeError("boom"))')

    result = run_case(case_dir, tmp_path)

    assert result.status == OracleStatus.ERROR
    assert "RuntimeError" in (result.phases[0].error or "")


def test_missing_oracle_class_reports_error(tmp_path: Path) -> None:
    case_dir = make_case(tmp_path)
    (case_dir / "oracles" / "oracle.py").write_text(
        "from evaluator.oracles.bases import CaseOracleEnvSetupBase\n"
        "class OracleEnvSetup(CaseOracleEnvSetupBase):\n"
        "    def requirements(self): return []\n",
        encoding="utf-8",
    )

    result = run_case(case_dir, tmp_path)

    assert result.status == OracleStatus.ERROR
    assert result.score == 0
