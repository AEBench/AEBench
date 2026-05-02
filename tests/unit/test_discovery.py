"""Oracle class discovery tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from evaluator.oracles.discovery import OracleLoadError, discover_oracle_classes


def write_oracle(oracle_dir: Path, filename: str, content: str) -> None:
    (oracle_dir / filename).write_text(textwrap.dedent(content), encoding="utf-8")


def make_case(tmp_path: Path) -> tuple[Path, Path]:
    case_dir = tmp_path / "case"
    oracle_dir = case_dir / "oracles"
    oracle_dir.mkdir(parents=True)
    return case_dir, oracle_dir


ENV_SETUP = """\
from evaluator.oracles.bases import CaseOracleEnvSetupBase

class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self):
        return []
"""

ARTIFACT_BUILD = """\
from evaluator.oracles.bases import CaseOracleArtifactBuildBase

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self):
        return []
"""

BENCHMARK_PREP = """\
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self):
        return []
"""

EXPERIMENT_RUNS = """\
from evaluator.oracles.bases import CaseOracleExperimentRunsBase

class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self):
        return []
"""


def test_discovers_all_four_oracle_classes(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(oracle_dir, "env_setup.py", ENV_SETUP)
    write_oracle(oracle_dir, "artifact_build.py", ARTIFACT_BUILD)
    write_oracle(oracle_dir, "benchmark_prep.py", BENCHMARK_PREP)
    write_oracle(oracle_dir, "experiment_runs.py", EXPERIMENT_RUNS)

    classes = discover_oracle_classes(case_dir)

    assert [item.name for item in classes] == [
        "env_setup",
        "artifact_build",
        "benchmark_prep",
        "experiment_runs",
    ]
    assert [item.cls.__name__ for item in classes] == [
        "OracleEnvSetup",
        "OracleArtifactBuild",
        "OracleBenchmarkPrep",
        "OracleExperimentRuns",
    ]


def test_discovery_uses_fixed_order_not_file_order(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(oracle_dir, "z_experiment_runs.py", EXPERIMENT_RUNS)
    write_oracle(oracle_dir, "y_benchmark_prep.py", BENCHMARK_PREP)
    write_oracle(oracle_dir, "x_artifact_build.py", ARTIFACT_BUILD)
    write_oracle(oracle_dir, "w_env_setup.py", ENV_SETUP)

    classes = discover_oracle_classes(case_dir)

    assert [item.name for item in classes] == [
        "env_setup",
        "artifact_build",
        "benchmark_prep",
        "experiment_runs",
    ]


def test_ignores_non_oracle_classes(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(
        oracle_dir,
        "env_setup.py",
        """\
        from evaluator.oracles.bases import CaseOracleEnvSetupBase

        class Helper:
            pass

        class OracleEnvSetup(CaseOracleEnvSetupBase):
            def requirements(self):
                return []
        """,
    )
    write_oracle(oracle_dir, "artifact_build.py", ARTIFACT_BUILD)
    write_oracle(oracle_dir, "benchmark_prep.py", BENCHMARK_PREP)
    write_oracle(oracle_dir, "experiment_runs.py", EXPERIMENT_RUNS)

    classes = discover_oracle_classes(case_dir)

    assert len(classes) == 4
    assert "Helper" not in [item.cls.__name__ for item in classes]
    assert [item.name for item in classes] == [
        "env_setup",
        "artifact_build",
        "benchmark_prep",
        "experiment_runs",
    ]


def test_imported_base_classes_are_not_discovered(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(oracle_dir, "env_setup.py", ENV_SETUP)

    classes = discover_oracle_classes(case_dir)

    assert [item.cls.__name__ for item in classes] == ["OracleEnvSetup"]


def test_duplicate_oracle_class_for_same_step_raises(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(oracle_dir, "env_setup_a.py", ENV_SETUP)
    write_oracle(oracle_dir, "env_setup_b.py", ENV_SETUP)

    with pytest.raises(OracleLoadError, match="duplicate"):
        discover_oracle_classes(case_dir)


def test_missing_oracle_dir_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    with pytest.raises(OracleLoadError, match="oracle directory is missing"):
        discover_oracle_classes(case_dir)


def test_empty_oracle_dir_raises(tmp_path: Path) -> None:
    case_dir, _oracle_dir = make_case(tmp_path)

    with pytest.raises(OracleLoadError, match="no Python modules"):
        discover_oracle_classes(case_dir)


def test_no_oracle_implementations_raises(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(oracle_dir, "common.py", "class Helper: pass\n")

    with pytest.raises(OracleLoadError, match="no oracle"):
        discover_oracle_classes(case_dir)


def test_bad_oracle_module_import_reports_load_error(tmp_path: Path) -> None:
    case_dir, oracle_dir = make_case(tmp_path)
    write_oracle(oracle_dir, "broken.py", "raise RuntimeError('boom')\n")

    with pytest.raises(OracleLoadError, match="failed to import"):
        discover_oracle_classes(case_dir)