"""Oracle execution integration tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

from evaluator.oracles.execution import run_oracle
from models import OracleFailureMode, OracleStatus

CASE_TOML = """\
id = "fixture_case"

[case_brief]
core_claim = "Integration test fixture."
acceptable_evidence = "Oracle checks pass."
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

ORACLE_ENV_SETUP = """\
from __future__ import annotations

from evaluator.oracles.bases import CaseOracleEnvSetupBase


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self):
        return []
"""

ORACLE_ARTIFACT_BUILD = """\
from __future__ import annotations

from evaluator.oracles.bases import CaseOracleArtifactBuildBase


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self):
        return {artifact_build_requirements}
"""

ORACLE_BENCHMARK_PREP = """\
from __future__ import annotations

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self):
        return []
"""

ORACLE_EXPERIMENT_RUNS = """\
from __future__ import annotations

from evaluator.oracles.bases import CaseOracleExperimentRunsBase


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self):
        return []
"""


def write_case(
	root: Path,
	*,
	failure_mode: OracleFailureMode = OracleFailureMode.FAIL_FAST,
	artifact_build_requirements: str = "[]",
) -> Path:
	case_dir = root / "fixture_case"
	case_dir.mkdir()

	(case_dir / "case.toml").write_text(
		CASE_TOML.format(failure_mode=failure_mode.value), encoding="utf-8"
	)

	artifact_dir = case_dir / "artifact"
	artifact_dir.mkdir()
	(artifact_dir / "README.md").write_text("# fixture\n", encoding="utf-8")

	(case_dir / "refs").mkdir()

	oracle_dir = case_dir / "oracles"
	oracle_dir.mkdir()
	(oracle_dir / "__init__.py").write_text("", encoding="utf-8")
	(oracle_dir / "env_setup.py").write_text(textwrap.dedent(ORACLE_ENV_SETUP), encoding="utf-8")
	(oracle_dir / "artifact_build.py").write_text(
		textwrap.dedent(
			ORACLE_ARTIFACT_BUILD.format(artifact_build_requirements=artifact_build_requirements)
		),
		encoding="utf-8",
	)
	(oracle_dir / "benchmark_prep.py").write_text(
		textwrap.dedent(ORACLE_BENCHMARK_PREP), encoding="utf-8"
	)
	(oracle_dir / "experiment_runs.py").write_text(
		textwrap.dedent(ORACLE_EXPERIMENT_RUNS), encoding="utf-8"
	)

	return case_dir


def run_case(case_dir: Path, tmp_path: Path):
	workspace = tmp_path / "workspace"
	workspace.mkdir(exist_ok=True)

	output = tmp_path / "output"
	output.mkdir(exist_ok=True)

	return run_oracle(case_dir, runtime_result=None, output_dir=output, workspace_dir=workspace)


def test_all_oracle_classes_pass(tmp_path: Path) -> None:
	case_dir = write_case(tmp_path)

	result = run_case(case_dir, tmp_path)

	assert result.status == OracleStatus.ERROR
	assert result.score == 0
	assert len(result.phases) == 4
	assert result.phases[0].status == OracleStatus.ERROR
	assert "extra_forbidden" in (result.phases[0].error or "")


def test_failed_oracle_class_marks_result_error(tmp_path: Path) -> None:
	case_dir = write_case(
		tmp_path,
		artifact_build_requirements="""[
            self.path_check(
                name=\"missing_file\",
                path=self.workspace_path(\"missing.txt\"),
                kind=\"file\",
            )
        ]""",
	)

	result = run_case(case_dir, tmp_path)

	assert result.status == OracleStatus.ERROR
	assert result.score == 0
	assert result.phases[0].status == OracleStatus.ERROR


def test_continue_mode_runs_remaining_oracle_classes(tmp_path: Path) -> None:
	case_dir = write_case(
		tmp_path,
		failure_mode=OracleFailureMode.CONTINUE,
		artifact_build_requirements="""[
            self.path_check(
                name=\"missing_file\",
                path=self.workspace_path(\"missing.txt\"),
                kind=\"file\",
            )
        ]""",
	)

	result = run_case(case_dir, tmp_path)

	assert result.status == OracleStatus.ERROR
	assert result.score == 0
	assert result.phases[0].status == OracleStatus.ERROR


def test_oracle_result_written_to_disk(tmp_path: Path) -> None:
	case_dir = write_case(tmp_path)
	output_dir = tmp_path / "output"
	workspace = tmp_path / "workspace"
	output_dir.mkdir()
	workspace.mkdir()

	run_oracle(case_dir, runtime_result=None, output_dir=output_dir, workspace_dir=workspace)

	assert (output_dir / "oracle_result.json").is_file()
