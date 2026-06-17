from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import execution as oracle_execution
from evaluator.oracles.discovery import DiscoveredOracleClass
from evaluator.oracles.reporting import CheckEntry, CheckOutcome
from models import OracleFailureMode, OracleInput, OracleStatus


@dataclass
class PhaseResult:
	phase: str
	status: OracleStatus
	summary: str = ""
	error: str | None = None
	checks: list[str] | None = None


@dataclass
class OracleResult:
	status: OracleStatus
	score: int | None = None
	summary: str = ""
	phases: list[PhaseResult] = None
	error: str | None = None


class DummyReport:
	def __init__(self, ok: bool, label: str):
		self.ok = ok
		self.results = [CheckEntry(name=label, outcome=CheckOutcome.PASSED, message="ok")]


class DummyPhase:
	def __init__(self, context, logger, *, ok: bool, label: str):
		self.context = context
		self.logger = logger
		self._ok = ok
		self._label = label

	def report(self):
		return DummyReport(self._ok, self._label)


class PassingPhase(DummyPhase):
	def __init__(self, context, logger):
		super().__init__(context, logger, ok=True, label="pass")


class FailingPhase(DummyPhase):
	def __init__(self, context, logger):
		super().__init__(context, logger, ok=False, label="fail")


class RuntimeErrorPhase(DummyPhase):
	def __init__(self, context, logger):
		raise RuntimeError("boom")


def _context(tmp_path: Path) -> OracleInput:
	return OracleInput(
		case_dir=tmp_path,
		artifact_dir=tmp_path / "artifact",
		workspace_dir=tmp_path / "workspace",
		output_dir=tmp_path / "output",
	)


def _classes(*phase_classes):
	keys = [
		("env_setup",),
		("artifact_build",),
		("benchmark_prep",),
		("experiment_runs",),
	]
	return [
		DiscoveredOracleClass(key=key, priority=index, cls=cls)
		for index, (key, cls) in enumerate(zip(keys, phase_classes, strict=True), start=1)
	]


def test_score_all_oracles_pass(monkeypatch, tmp_path: Path) -> None:
	monkeypatch.setattr(oracle_execution, "OraclePhaseResult", PhaseResult)
	monkeypatch.setattr(oracle_execution, "OracleResult", OracleResult)

	result = oracle_execution.run_oracle_classes(
		_context(tmp_path),
		classes=_classes(PassingPhase, PassingPhase, PassingPhase, PassingPhase),
		failure_mode=OracleFailureMode.FAIL_FAST,
	)

	assert result.status == OracleStatus.SUCCESS
	assert result.score == 4
	assert [phase.status for phase in result.phases] == [
		OracleStatus.SUCCESS,
		OracleStatus.SUCCESS,
		OracleStatus.SUCCESS,
		OracleStatus.SUCCESS,
	]


def test_score_partial_oracles_pass_in_continue_mode(monkeypatch, tmp_path: Path) -> None:
	monkeypatch.setattr(oracle_execution, "OraclePhaseResult", PhaseResult)
	monkeypatch.setattr(oracle_execution, "OracleResult", OracleResult)

	result = oracle_execution.run_oracle_classes(
		_context(tmp_path),
		classes=_classes(PassingPhase, FailingPhase, PassingPhase, PassingPhase),
		failure_mode=OracleFailureMode.CONTINUE,
	)

	assert result.status == OracleStatus.ERROR
	assert result.score == 3
	assert [phase.status for phase in result.phases] == [
		OracleStatus.SUCCESS,
		OracleStatus.ERROR,
		OracleStatus.SUCCESS,
		OracleStatus.SUCCESS,
	]


def test_score_zero_when_all_oracles_fail_in_continue_mode(monkeypatch, tmp_path: Path) -> None:
	monkeypatch.setattr(oracle_execution, "OraclePhaseResult", PhaseResult)
	monkeypatch.setattr(oracle_execution, "OracleResult", OracleResult)

	result = oracle_execution.run_oracle_classes(
		_context(tmp_path),
		classes=_classes(FailingPhase, FailingPhase, FailingPhase, FailingPhase),
		failure_mode=OracleFailureMode.CONTINUE,
	)

	assert result.status == OracleStatus.ERROR
	assert result.score == 0
	assert all(phase.status == OracleStatus.ERROR for phase in result.phases)


def test_fail_fast_marks_remaining_oracles_pending(monkeypatch, tmp_path: Path) -> None:
	monkeypatch.setattr(oracle_execution, "OraclePhaseResult", PhaseResult)
	monkeypatch.setattr(oracle_execution, "OracleResult", OracleResult)

	result = oracle_execution.run_oracle_classes(
		_context(tmp_path),
		classes=_classes(FailingPhase, PassingPhase, PassingPhase, PassingPhase),
		failure_mode=OracleFailureMode.FAIL_FAST,
	)

	assert result.status == OracleStatus.ERROR
	assert result.score == 0
	assert [phase.status for phase in result.phases] == [
		OracleStatus.ERROR,
		OracleStatus.PENDING,
		OracleStatus.PENDING,
		OracleStatus.PENDING,
	]


def test_requirements_exception_is_captured(monkeypatch, tmp_path: Path) -> None:
	monkeypatch.setattr(oracle_execution, "OraclePhaseResult", PhaseResult)
	monkeypatch.setattr(oracle_execution, "OracleResult", OracleResult)

	result = oracle_execution.run_oracle_classes(
		_context(tmp_path),
		classes=_classes(RuntimeErrorPhase, PassingPhase, PassingPhase, PassingPhase),
		failure_mode=OracleFailureMode.FAIL_FAST,
	)

	assert result.status == OracleStatus.ERROR
	assert result.phases[0].error == "RuntimeError: boom"
	assert [phase.status for phase in result.phases] == [
		OracleStatus.ERROR,
		OracleStatus.PENDING,
		OracleStatus.PENDING,
		OracleStatus.PENDING,
	]
