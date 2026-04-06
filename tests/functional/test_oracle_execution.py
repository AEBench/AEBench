"""Oracle execution with stub phases tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from evaluator.oracles.case_base import (
	CaseOracleArtifactBuildBase,
	CaseOracleEnvSetupBase,
)
from evaluator.oracles.discovery import (
	ARTIFACT_BUILD,
	ENV_SETUP,
	ORACLE_PHASE_PRIORITIES,
	DiscoveredPhase,
)
from evaluator.oracles.execution import run_phases
from evaluator.oracles.env_setup_checks import FilesystemPathCheck
from models import OracleInput, OracleFailureMode, OracleStatus


class _PassEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self):
		return []


class _PassArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self):
		return []


class _FailEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self):
		return [FilesystemPathCheck(
			name="will_not_exist",
			path=Path("/tmp/__nonexistent_functional_test_xyz__"),
		)]


def _ctx(tmp_path: Path) -> OracleInput:
	return OracleInput(
		case_dir=tmp_path,
		artifact_dir=tmp_path / "artifact",
		workspace_dir=tmp_path / "workspace",
		output_dir=tmp_path / "output",
	)


def _phase(key, cls) -> DiscoveredPhase:
	return DiscoveredPhase(
		key=key,
		priority=ORACLE_PHASE_PRIORITIES[key],
		cls=cls,
		module_name="test",
		qualname=cls.__qualname__,
	)


def test_all_passing_returns_success_status(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert result.status == OracleStatus.SUCCESS


def test_all_passing_score_equals_phase_count(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert result.score == len(phases)


def test_result_contains_one_entry_per_phase(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert len(result.phases) == len(phases)


def test_oracle_result_status_error_on_any_failure(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _FailEnvSetup)]
	result = run_phases(_ctx(tmp_path), phases=phases, failure_mode=OracleFailureMode.CONTINUE)
	assert result.status == OracleStatus.ERROR


def test_phase_summary_contains_phase_name(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert "env_setup" in result.phases[0].summary


def test_overall_summary_reports_score(tmp_path: Path) -> None:
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert "2/2" in result.summary


def test_exception_in_report_captured_as_error_phase(tmp_path: Path) -> None:
	"""Exception in report() is caught, phase gets ERROR status."""
	class _BoomReport(CaseOracleEnvSetupBase):
		def requirements(self):
			return []

		def report(self):  # type: ignore[override]
			raise ValueError("report() exploded")

	phases = [_phase(ENV_SETUP, _BoomReport)]
	result = run_phases(_ctx(tmp_path), phases=phases)

	assert result.phases[0].status == OracleStatus.ERROR
	assert "ValueError" in (result.phases[0].error or "")


def test_exception_error_field_captures_exception_type(tmp_path: Path) -> None:
	class _OOMReport(CaseOracleEnvSetupBase):
		def requirements(self):
			return []

		def report(self):  # type: ignore[override]
			raise MemoryError("out of memory in report()")

	phases = [_phase(ENV_SETUP, _OOMReport)]
	result = run_phases(_ctx(tmp_path), phases=phases)
	assert "MemoryError" in (result.phases[0].error or "")


def test_on_phase_start_called_for_each_phase(tmp_path: Path) -> None:
	started: list[str] = []
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	run_phases(
		_ctx(tmp_path),
		phases=phases,
		on_phase_start=lambda p: started.append(p.name),
	)
	assert started == ["env_setup", "artifact_build"]


def test_on_phase_finish_called_with_result(tmp_path: Path) -> None:
	finished: list[tuple[str, OracleStatus]] = []
	phases = [_phase(ENV_SETUP, _PassEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	run_phases(
		_ctx(tmp_path),
		phases=phases,
		on_phase_finish=lambda p, r: finished.append((p.name, r.status)),
	)
	assert finished == [("env_setup", OracleStatus.SUCCESS), ("artifact_build", OracleStatus.SUCCESS)]


def test_on_phase_start_not_called_for_skipped_phases(tmp_path: Path) -> None:
	started: list[str] = []
	phases = [_phase(ENV_SETUP, _FailEnvSetup), _phase(ARTIFACT_BUILD, _PassArtifactBuild)]
	run_phases(
		_ctx(tmp_path),
		phases=phases,
		failure_mode=OracleFailureMode.FAIL_FAST,
		on_phase_start=lambda p: started.append(p.name),
	)
	assert "artifact_build" not in started
