"""Oracle execution with stub phases tests."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.case_base import (
	CaseOracleArtifactBuildBase,
	CaseOracleBenchmarkPrepBase,
	CaseOracleEnvSetupBase,
	CaseOracleExperimentRunsBase,
)
from evaluator.oracles.discovery import (
	ARTIFACT_BUILD,
	BENCHMARK_PREP,
	ENV_SETUP,
	EXPERIMENT_RUNS,
	DiscoveredPhase,
)
from evaluator.oracles.env_setup_checks import FilesystemPathCheck
from evaluator.oracles.execution import run_phases
from evaluator.oracles.utils import BaseCheck
from models import OracleFailureMode, OracleInput, OracleStatus


class _PassEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _PassArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _PassBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _PassExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _FailEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return [
			FilesystemPathCheck(
				name="will_not_exist",
				path=Path("/tmp/__nonexistent_functional_test_xyz__"),
			)
		]


def _ctx(tmp_path: Path) -> OracleInput:
	return OracleInput(
		case_dir=tmp_path,
		artifact_dir=tmp_path / "artifact",
		workspace_dir=tmp_path / "workspace",
		output_dir=tmp_path / "output",
	)


def _all_pass_phases() -> list[DiscoveredPhase]:
	return [
		DiscoveredPhase.from_class(_PassEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_PassArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_PassBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_PassExperimentRuns, EXPERIMENT_RUNS),
	]


def _phases_with(overrides: dict[tuple[str, ...], type]) -> list[DiscoveredPhase]:
	"""All four phases, with specific phases replaced by the given classes."""
	defaults: dict[tuple[str, ...], type] = {
		ENV_SETUP: _PassEnvSetup,
		ARTIFACT_BUILD: _PassArtifactBuild,
		BENCHMARK_PREP: _PassBenchmarkPrep,
		EXPERIMENT_RUNS: _PassExperimentRuns,
	}
	defaults.update(overrides)
	return [DiscoveredPhase.from_class(cls, key) for key, cls in defaults.items()]


def test_all_passing_returns_success_status(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=_all_pass_phases())
	assert result.status == OracleStatus.SUCCESS


def test_all_passing_score_equals_phase_count(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=_all_pass_phases())
	assert result.score == 4


def test_result_contains_one_entry_per_phase(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=_all_pass_phases())
	assert len(result.phases) == 4


def test_oracle_result_status_error_on_any_failure(tmp_path: Path) -> None:
	phases = _phases_with({ENV_SETUP: _FailEnvSetup})
	result = run_phases(_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE)
	assert result.status == OracleStatus.ERROR


def test_phase_summary_contains_phase_name(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=_all_pass_phases())
	assert "env_setup" in result.phases[0].summary


def test_overall_summary_reports_score(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=_all_pass_phases())
	assert "4/4" in result.summary


def test_exception_in_requirements_captured_as_error_phase(tmp_path: Path) -> None:
	"""Exception in requirements() is caught, phase gets ERROR status."""

	class _BoomRequirements(CaseOracleEnvSetupBase):
		def requirements(self) -> Sequence[BaseCheck]:
			raise ValueError("requirements() exploded")

	phases = _phases_with({ENV_SETUP: _BoomRequirements})
	result = run_phases(_ctx(tmp_path), phases=phases)

	assert result.phases[0].status == OracleStatus.ERROR
	assert "ValueError" in (result.phases[0].error or "")


def test_exception_error_field_captures_exception_type(tmp_path: Path) -> None:
	class _OOMRequirements(CaseOracleEnvSetupBase):
		def requirements(self) -> Sequence[BaseCheck]:
			raise MemoryError("out of memory in requirements()")

	phases = _phases_with({ENV_SETUP: _OOMRequirements})
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert "MemoryError" in (result.phases[0].error or "")


def test_on_phase_start_called_for_each_phase(tmp_path: Path) -> None:
	started: list[str] = []
	run_phases(
		_ctx(tmp_path),
		phases=_all_pass_phases(),
		on_phase_start=lambda p: started.append(p.name),
	)
	assert started == ["env_setup", "artifact_build", "benchmark_prep", "experiment_runs"]


def test_on_phase_finish_called_with_result(tmp_path: Path) -> None:
	finished: list[tuple[str, OracleStatus]] = []
	run_phases(
		_ctx(tmp_path),
		phases=_all_pass_phases(),
		on_phase_finish=lambda p, r: finished.append((p.name, r.status)),
	)
	assert finished == [
		("env_setup", OracleStatus.SUCCESS),
		("artifact_build", OracleStatus.SUCCESS),
		("benchmark_prep", OracleStatus.SUCCESS),
		("experiment_runs", OracleStatus.SUCCESS),
	]


def test_on_phase_start_not_called_for_skipped_phases(tmp_path: Path) -> None:
	started: list[str] = []
	phases = _phases_with({ENV_SETUP: _FailEnvSetup})
	run_phases(
		_ctx(tmp_path),
		phases=phases,
		failure_mode=OracleFailureMode.FAIL_FAST,
		on_phase_start=lambda p: started.append(p.name),
	)
	assert "artifact_build" not in started
