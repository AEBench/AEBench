from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator.oracles import checks
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.process import ProcResult
from evaluator.oracles.reporting import Check, CheckResult, build_oracle_report


@dataclass
class FakeExecutor:
	path_separator: str = ":"
	resolved: str | None = "/bin/fake"
	env: dict[str, str] | None = None
	exists: bool = True
	is_file: bool = True
	is_dir: bool = False
	stdout: str = ""
	stderr: str = ""
	returncode: int = 0

	def resolve_executable(self, executable: str, *, env=None):
		return self.resolved

	def read_env_var(self, name: str, *, env=None):
		if self.env is None:
			return None
		return self.env.get(name)

	def run_process_capture(
		self,
		*,
		cmd,
		cwd,
		env,
		timeout_seconds,
		use_shell=False,
		capture_limit_chars=16384,
		drain_after_kill=False,
		encoding=None,
		on_chunk=None,
	):
		return ProcResult(
			returncode=self.returncode, stdout=self.stdout, stderr=self.stderr, timed_out=False
		)

	def path_exists(self, path):
		return self.exists

	def path_is_file(self, path):
		return self.is_file

	def path_is_dir(self, path):
		return self.is_dir

	def read_file_text(self, path, encoding="utf-8"):
		return "content"

	def close(self):
		return None


class _FakeRegistry:
	def __init__(self, executors: dict[str, FakeExecutor]) -> None:
		self.executors = executors

	def executor_for(self, target: str) -> FakeExecutor:
		return self.executors[target]


class _TestOracle(CaseOracleEnvSetupBase):
	def requirements(self):
		return ()


def test_version_check_success() -> None:
	executor = FakeExecutor(stdout="tool version 1.2.3")
	chk = checks.VersionCheck(name="tool", cmd=["tool"], min_version=(1, 2, 0))
	result = chk.check(executor)
	assert result.ok is True


def test_version_check_failure_for_low_version() -> None:
	executor = FakeExecutor(stdout="tool version 1.1.9")
	chk = checks.VersionCheck(name="tool", cmd=["tool"], min_version=(1, 2, 0))
	result = chk.check(executor)
	assert result.ok is False
	assert "does not satisfy" in result.message


def test_env_var_check_exact_match() -> None:
	executor = FakeExecutor(env={"EGWALKER_HOME": "/tmp/egwalker"})
	chk = checks.EnvVarCheck(name="env", env_var="EGWALKER_HOME", expected="/tmp/egwalker")
	result = chk.check(executor)
	assert result.ok is True


def test_env_var_check_contains_match() -> None:
	executor = FakeExecutor(env={"EGWALKER_HOME": "/opt/egwalker/bin"})
	chk = checks.EnvVarCheck(
		name="env",
		env_var="EGWALKER_HOME",
		expected="egwalker",
		match_mode=checks.EnvMatchMode.CONTAINS,
	)
	result = chk.check(executor)
	assert result.ok is True


def test_path_check_file_and_directory() -> None:
	file_exec = FakeExecutor(exists=True, is_file=True, is_dir=False)
	dir_exec = FakeExecutor(exists=True, is_file=False, is_dir=True)

	file_check = checks.PathCheck(name="file", path=Path("README.md"), kind=checks.PathKind.FILE)
	dir_check = checks.PathCheck(name="dir", path=Path("."), kind=checks.PathKind.DIRECTORY)

	assert file_check.check(file_exec).ok is True
	assert dir_check.check(dir_exec).ok is True


def test_path_check_reports_missing_path() -> None:
	executor = FakeExecutor(exists=False, is_file=False, is_dir=False)
	chk = checks.PathCheck(name="path", path=Path("missing.txt"), kind=checks.PathKind.FILE)
	result = chk.check(executor)
	assert result.ok is False
	assert "not found" in result.message


def test_build_oracle_report_passes_phase_executor_to_callable() -> None:
	executor = FakeExecutor()
	seen: list[FakeExecutor] = []
	check = Check(
		name="records_executor",
		fn=lambda received: seen.append(received) or CheckResult.success(),
	)

	report = build_oracle_report(
		logger=logging.getLogger(__name__),
		requirements=lambda: (check,),
		executor=executor,  # type: ignore[arg-type]
	)

	assert report.ok is True
	assert seen == [executor]


def test_oracle_check_target_defaults_and_explicit_override(tmp_path: Path) -> None:
	phase = FakeExecutor(exists=False, is_file=False)
	alternate = FakeExecutor(exists=True, is_file=True)
	context = SimpleNamespace(
		case_dir=tmp_path / "case",
		artifact_dir=tmp_path / "artifact",
		workspace_dir=tmp_path / "workspace",
		output_dir=tmp_path / "output",
		runtime_registry=_FakeRegistry({"phase": phase, "alternate": alternate}),
		oracle_phase_targets=SimpleNamespace(target_for_phase=lambda _phase: "phase"),
	)
	oracle = _TestOracle(context=context, logger=logging.getLogger(__name__))  # type: ignore[arg-type]

	default_check = oracle.path_check(
		name="default", path=Path("README.md"), kind=checks.PathKind.FILE
	)
	targeted_check = oracle.path_check(
		name="targeted",
		path=Path("README.md"),
		kind=checks.PathKind.FILE,
		target="alternate",
	)

	assert oracle.executor_for() is phase
	assert default_check.check(phase).ok is False  # type: ignore[arg-type]
	assert targeted_check.check(phase).ok is True  # type: ignore[arg-type]
	with pytest.raises(KeyError):
		oracle.executor_for("missing")
