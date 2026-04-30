"""Oracle phase scoring and failure mode tests."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from evaluator.oracles.bases import (
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


class _FailEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return [
			FilesystemPathCheck(
				name="nonexistent",
				path=Path("/tmp/__nonexistent_for_scoring_test_xyz__"),
			)
		]


class _PassArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _FailArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return [
			FilesystemPathCheck(
				name="nonexistent_artifact",
				path=Path("/tmp/__nonexistent_artifact_xyz__"),
			)
		]


class _PassBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _PassExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return []


class _FailBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return [
			FilesystemPathCheck(
				name="nonexistent_prep",
				path=Path("/tmp/__nonexistent_prep_xyz__"),
			)
		]


class _FailExperimentRuns(CaseOracleExperimentRunsBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return [
			FilesystemPathCheck(
				name="nonexistent_runs",
				path=Path("/tmp/__nonexistent_runs_xyz__"),
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
	"""All four phases, all passing."""
	return [
		DiscoveredPhase.from_class(_PassEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_PassArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_PassBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_PassExperimentRuns, EXPERIMENT_RUNS),
	]


def _all_fail_phases() -> list[DiscoveredPhase]:
	"""All four phases, all failing."""
	return [
		DiscoveredPhase.from_class(_FailEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_FailArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_FailBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_FailExperimentRuns, EXPERIMENT_RUNS),
	]


def test_score_all_phases_pass(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=_all_pass_phases())
	assert result.score == 4
	assert result.status == OracleStatus.SUCCESS


def test_score_partial_phases_pass(tmp_path: Path) -> None:
	phases = [
		DiscoveredPhase.from_class(_PassEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_FailArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_PassBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_PassExperimentRuns, EXPERIMENT_RUNS),
	]
	result = run_phases(_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE)
	assert result.score == 3
	assert result.status == OracleStatus.ERROR


def test_score_no_phases_pass(tmp_path: Path) -> None:
	result = run_phases(
		_ctx(tmp_path), phases=_all_fail_phases(), failure_mode=OracleFailureMode.CONTINUE
	)
	assert result.score == 0
	assert result.status == OracleStatus.ERROR


def test_missing_phases_raises(tmp_path: Path) -> None:
	"""run_phases rejects an incomplete phase set."""
	phases = [
		DiscoveredPhase.from_class(_PassEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_PassArtifactBuild, ARTIFACT_BUILD),
	]
	with pytest.raises(ValueError, match="missing required phases"):
		run_phases(_ctx(tmp_path), phases=phases)


def test_fail_fast_stops_after_first_failure(tmp_path: Path) -> None:
	phases = [
		DiscoveredPhase.from_class(_FailEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_PassArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_PassBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_PassExperimentRuns, EXPERIMENT_RUNS),
	]
	result = run_phases(_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.FAIL_FAST)
	assert len(result.phases) == 4
	assert result.phases[0].status == OracleStatus.ERROR
	assert result.phases[1].status == OracleStatus.PENDING
	assert result.phases[2].status == OracleStatus.PENDING
	assert result.phases[3].status == OracleStatus.PENDING
	assert result.score == 0


def test_continue_mode_runs_all_phases(tmp_path: Path) -> None:
	phases = [
		DiscoveredPhase.from_class(_FailEnvSetup, ENV_SETUP),
		DiscoveredPhase.from_class(_PassArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_PassBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_PassExperimentRuns, EXPERIMENT_RUNS),
	]
	result = run_phases(_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE)
	assert len(result.phases) == 4
	assert result.phases[0].status == OracleStatus.ERROR
	assert result.phases[1].status == OracleStatus.SUCCESS
	assert result.score == 3


def test_phase_exception_is_captured_not_raised(tmp_path: Path) -> None:
	"""Exception in requirements() is caught, phase gets ERROR status."""

	class _BoomRequirements(CaseOracleEnvSetupBase):
		def requirements(self) -> Sequence[BaseCheck]:
			raise RuntimeError("requirements() exploded unexpectedly")

	phases = [
		DiscoveredPhase.from_class(_BoomRequirements, ENV_SETUP),
		DiscoveredPhase.from_class(_PassArtifactBuild, ARTIFACT_BUILD),
		DiscoveredPhase.from_class(_PassBenchmarkPrep, BENCHMARK_PREP),
		DiscoveredPhase.from_class(_PassExperimentRuns, EXPERIMENT_RUNS),
	]
	result = run_phases(_ctx(tmp_path), phases=phases)

	assert result.phases[0].status == OracleStatus.ERROR
	assert "RuntimeError" in (result.phases[0].error or "")


def test_phase_error_message_included_in_oracle_error(tmp_path: Path) -> None:
	result = run_phases(
		_ctx(tmp_path), phases=_all_fail_phases(), failure_mode=OracleFailureMode.CONTINUE
	)
	assert result.error is not None
	assert "env_setup" in result.error or "artifact_build" in result.error
