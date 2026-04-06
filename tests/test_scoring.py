"""Oracle phase scoring and failure mode tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from artevalbench.evaluator.oracles.case_base import (
	CaseOracleArtifactBuildBase,
	CaseOracleEnvSetupBase,
)
from artevalbench.evaluator.oracles.discovery import (
	ARTIFACT_BUILD,
	ENV_SETUP,
	ORACLE_PHASE_PRIORITIES,
	DiscoveredPhase,
)
from artevalbench.evaluator.oracles.execution import run_phases
from artevalbench.evaluator.oracles.env_setup_checks import FilesystemPathCheck
from artevalbench.models import OracleInput, OracleFailureMode, OracleStatus


class _PassEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self):
		return []


class _FailEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self):
		return [FilesystemPathCheck(
			name="nonexistent",
			path=Path("/tmp/__nonexistent_for_scoring_test_xyz__"),
		)]


class _PassArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self):
		return []


class _FailArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self):
		return [FilesystemPathCheck(
			name="nonexistent_artifact",
			path=Path("/tmp/__nonexistent_artifact_xyz__"),
		)]


def _ctx(tmp_path: Path) -> OracleInput:
	return OracleInput(
		case_dir=tmp_path,
		artifact_dir=tmp_path / "artifact",
		workspace_dir=tmp_path / "workspace",
		output_dir=tmp_path / "output",
	)


def _phase(key, cls, priority_offset: int = 0) -> DiscoveredPhase:
	return DiscoveredPhase(
		key=key,
		priority=ORACLE_PHASE_PRIORITIES[key] + priority_offset,
		cls=cls,
		module_name="test_stub",
		qualname=cls.__qualname__,
	)


def test_score_all_phases_pass(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert result.score == 2
	assert result.status == OracleStatus.SUCCESS


def test_score_partial_phases_pass(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _FailArtifactBuild)]
	result = run_phases(
		_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE
	)
	assert result.score == 1
	assert result.status == OracleStatus.ERROR


def test_score_no_phases_pass(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _FailEnvSetup), _phase(ARTIFACT_BUILD, _FailArtifactBuild)]
	result = run_phases(
		_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE
	)
	assert result.score == 0
	assert result.status == OracleStatus.ERROR


def test_no_phases_configured_score_zero(tmp_path: Path) -> None:
	result = run_phases(_ctx(tmp_path), phases=[])
	assert result.score == 0
	assert result.status == OracleStatus.SUCCESS  # vacuously succeeded


def test_fail_fast_stops_after_first_failure(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _FailEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(
		_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.FAIL_FAST
	)
	assert len(result.phases) == 2
	assert result.phases[0].status == OracleStatus.ERROR
	assert result.phases[1].status == OracleStatus.PENDING
	assert result.score == 0


def test_continue_mode_runs_all_phases(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _FailEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(
		_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE
	)
	assert len(result.phases) == 2
	assert result.phases[0].status == OracleStatus.ERROR
	assert result.phases[1].status == OracleStatus.SUCCESS
	assert result.score == 1


def test_phase_exception_is_captured_not_raised(tmp_path: Path) -> None:
	"""Exception in report() is caught, phase gets ERROR status."""
	class _BoomReport(CaseOracleEnvSetupBase):
		def requirements(self):
			return []

		def report(self):  # type: ignore[override]
			raise RuntimeError("report() exploded unexpectedly")

	phases = [_phase(ENV_SETUP, _BoomReport)]
	result = run_phases(_ctx(tmp_path), phases=phases)

	assert result.phases[0].status == OracleStatus.ERROR
	assert "RuntimeError" in (result.phases[0].error or "")
	assert result.score == 0


def test_phase_error_message_included_in_oracle_error(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _FailEnvSetup), _phase(ARTIFACT_BUILD, _FailArtifactBuild)]
	result = run_phases(
		_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE
	)
	assert result.error is not None
	assert "env_setup" in result.error or "artifact_build" in result.error
