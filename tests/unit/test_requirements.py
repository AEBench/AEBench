"""Oracle internal check logic tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.benchmark_prep_checks import BenchmarkCommandCheck
from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	EnvironmentPathEntryCheck,
	EnvironmentVariableCheck,
	EnvMatchMode,
	FilesystemPathCheck,
	PathEntryMatchMode,
	PathType,
	VersionCompare,
)
from evaluator.oracles.utils import CheckResult, ProcResult, RuntimeCheckExecutor


class FakeRuntimeExecutor(RuntimeCheckExecutor):
	def __init__(
		self,
		*,
		resolved: str | None = None,
		env: Mapping[str, str] | None = None,
		proc_result: ProcResult | None = None,
		path_separator: str = ":",
	) -> None:
		self._resolved = resolved
		self._env = dict(env or {})
		self._proc_result = proc_result or ProcResult(
			returncode=0,
			stdout="",
			stderr="",
			timed_out=False,
		)
		self._path_separator = path_separator
		self.calls: list[tuple[str, object]] = []

	@property
	def path_separator(self) -> str:
		return self._path_separator

	def resolve_executable(
		self,
		executable: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		self.calls.append(("resolve_executable", (executable, dict(env or {}))))
		return self._resolved

	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		self.calls.append(("read_env_var", (name, dict(env or {}))))
		if env is not None and name in env:
			return env[name]
		return self._env.get(name)

	def run_process_capture(
		self,
		*,
		cmd: str | Sequence[str],
		cwd: Path | None,
		env: Mapping[str, str] | None,
		timeout_seconds: float,
		use_shell: bool = False,
		capture_limit_chars: int = 16_384,
		drain_after_kill: bool = False,
		encoding: str | None = None,
		on_chunk: Callable[[str, str], None] | None = None,
	) -> ProcResult:
		self.calls.append(
			(
				"run_process_capture",
				{
					"cmd": cmd,
					"cwd": cwd,
					"env": dict(env or {}),
					"timeout_seconds": timeout_seconds,
					"use_shell": use_shell,
					"capture_limit_chars": capture_limit_chars,
					"drain_after_kill": drain_after_kill,
					"encoding": encoding,
					"on_chunk": on_chunk,
				},
			)
		)
		if on_chunk is not None:
			if self._proc_result.stdout:
				on_chunk("stdout", self._proc_result.stdout)
			if self._proc_result.stderr:
				on_chunk("stderr", self._proc_result.stderr)
		return self._proc_result

	def path_exists(self, path: Path) -> bool:
		self.calls.append(("path_exists", path))
		return path.exists()

	def path_is_file(self, path: Path) -> bool:
		self.calls.append(("path_is_file", path))
		return path.is_file()

	def path_is_dir(self, path: Path) -> bool:
		self.calls.append(("path_is_dir", path))
		return path.is_dir()

	def read_file_text(self, path: Path, encoding: str = "utf-8") -> str:
		self.calls.append(("read_file_text", path))
		return path.read_text(encoding=encoding)

	def close(self) -> None:
		self.calls.append(("close", None))


class _InvalidEnvVarExecutor(FakeRuntimeExecutor):
	def read_env_var(
		self,
		name: str,
		*,
		env: Mapping[str, str] | None = None,
	) -> str | None:
		raise ValueError(f"invalid environment variable name: {name!r}")


def test_check_result_success_ok_true() -> None:
	r = CheckResult.success("all good")
	assert r.ok is True


def test_check_result_success_message_preserved() -> None:
	r = CheckResult.success("msg")
	assert r.message == "msg"


def test_check_result_failure_ok_false() -> None:
	r = CheckResult.failure("something broke")
	assert r.ok is False


def test_check_result_failure_message_preserved() -> None:
	r = CheckResult.failure("reason")
	assert r.message == "reason"


def test_check_result_returncode_preserved() -> None:
	assert CheckResult.success(returncode=0).returncode == 0
	assert CheckResult.failure("f", returncode=2).returncode == 2


def test_check_result_timed_out_flag() -> None:
	r = CheckResult.failure("timeout", timed_out=True)
	assert r.timed_out is True
	assert CheckResult.success().timed_out is False


def test_filesystem_path_exists(tmp_path: Path) -> None:
	f = tmp_path / "exists.txt"
	f.write_text("hello")
	req = FilesystemPathCheck(name="check", path=f)
	assert req.check().ok


def test_filesystem_path_missing(tmp_path: Path) -> None:
	req = FilesystemPathCheck(name="check", path=tmp_path / "no_such_file.txt")
	result = req.check()
	assert not result.ok
	assert result.message  # descriptive, non-empty


def test_filesystem_path_type_file_correct(tmp_path: Path) -> None:
	f = tmp_path / "file.txt"
	f.write_text("data")
	req = FilesystemPathCheck(name="check", path=f, path_type=PathType.FILE)
	assert req.check().ok


def test_filesystem_path_type_file_on_directory(tmp_path: Path) -> None:
	d = tmp_path / "subdir"
	d.mkdir()
	req = FilesystemPathCheck(name="check", path=d, path_type=PathType.FILE)
	assert not req.check().ok


def test_filesystem_path_type_directory_correct(tmp_path: Path) -> None:
	d = tmp_path / "subdir"
	d.mkdir()
	req = FilesystemPathCheck(name="check", path=d, path_type=PathType.DIRECTORY)
	assert req.check().ok


def test_filesystem_path_type_directory_on_file(tmp_path: Path) -> None:
	f = tmp_path / "file.txt"
	f.write_text("x")
	req = FilesystemPathCheck(name="check", path=f, path_type=PathType.DIRECTORY)
	assert not req.check().ok


def test_dependency_version_sufficient() -> None:
	req = DependencyVersionCheck(
		name="python_sufficient",
		cmd=("python3", "--version"),
		required_version=(1, 0, 0),
		compare=VersionCompare.GEQ,
	)
	assert req.check().ok


def test_dependency_version_insufficient() -> None:
	req = DependencyVersionCheck(
		name="python_insufficient",
		cmd=("python3", "--version"),
		required_version=(99, 0, 0),
		compare=VersionCompare.GEQ,
	)
	result = req.check()
	assert not result.ok
	assert "does not satisfy" in result.message


def test_dependency_version_not_found_on_path() -> None:
	req = DependencyVersionCheck(
		name="nonexistent_tool",
		cmd=("__nonexistent_binary_xyz__", "--version"),
		required_version=(1, 0, 0),
	)
	result = req.check()
	assert not result.ok
	assert result.message


def test_dependency_version_eq_compare() -> None:
	import sys

	major, minor, patch = sys.version_info[:3]
	req = DependencyVersionCheck(
		name="python_eq",
		cmd=("python3", "--version"),
		required_version=(major, minor, patch),
		compare=VersionCompare.EQ,
	)
	assert req.check().ok


def test_dependency_version_eq_compare_wrong_version() -> None:
	req = DependencyVersionCheck(
		name="python_eq_wrong",
		cmd=("python3", "--version"),
		required_version=(0, 0, 1),
		compare=VersionCompare.EQ,
	)
	assert not req.check().ok


def test_dependency_version_uses_runtime_executor() -> None:
	executor = FakeRuntimeExecutor(
		resolved="/usr/bin/python3",
		proc_result=ProcResult(
			returncode=0,
			stdout="Python 3.11.9\n",
			stderr="",
			timed_out=False,
		),
	)
	req = DependencyVersionCheck(
		name="python_executor",
		cmd=("python3", "--version"),
		required_version=(3, 11, 0),
		compare=VersionCompare.GEQ,
		executor=executor,
	)
	assert req.check().ok
	assert [name for name, _payload in executor.calls] == [
		"resolve_executable",
		"run_process_capture",
	]


def test_dependency_version_rejects_two_tuple() -> None:
	with pytest.raises(ValueError, match="3-tuple of non-negative ints"):
		# Intentionally ill-defined args to test DependencyVersionCheck resiliency
		DependencyVersionCheck(
			name="t",
			cmd=("x",),
			required_version=(3, 11),  # type: ignore[arg-type]
		)


def test_dependency_version_rejects_string_element() -> None:
	with pytest.raises(ValueError, match="3-tuple of non-negative ints"):
		# Intentionally ill-defined args to test DependencyVersionCheck resiliency
		DependencyVersionCheck(
			name="t",
			cmd=("x",),
			required_version=("3", 11, 0),  # type: ignore[arg-type]
		)


def test_dependency_version_rejects_negative() -> None:
	with pytest.raises(ValueError, match="3-tuple of non-negative ints"):
		DependencyVersionCheck(name="t", cmd=("x",), required_version=(-1, 0, 0))


def test_environment_variable_uses_runtime_executor() -> None:
	executor = FakeRuntimeExecutor(env={"PYTHON_VERSION": "3.11.9"})
	req = EnvironmentVariableCheck(
		name="python_env",
		env_var="PYTHON_VERSION",
		expected="3.11",
		match_mode=EnvMatchMode.CONTAINS,
		executor=executor,
	)
	assert req.check().ok
	assert [name for name, _payload in executor.calls] == ["read_env_var"]


def test_environment_path_entry_uses_runtime_executor() -> None:
	executor = FakeRuntimeExecutor(
		env={"PATH": "/repo/.venv/bin:/usr/bin:/bin"},
		path_separator=":",
	)
	req = EnvironmentPathEntryCheck(
		name="path_entry",
		env_var="PATH",
		expected="/repo/.venv/bin",
		match_mode=PathEntryMatchMode.EXACT,
		executor=executor,
	)
	assert req.check().ok
	assert [name for name, _payload in executor.calls] == ["read_env_var"]


def test_environment_path_entry_invalid_runtime_name_becomes_failure() -> None:
	req = EnvironmentPathEntryCheck(
		name="bad_path_env",
		env_var="BAD-NAME",
		expected="/repo/.venv/bin",
		executor=_InvalidEnvVarExecutor(),
	)
	result = req.check()
	assert not result.ok
	assert "invalid environment variable name" in result.message


def test_environment_variable_invalid_runtime_name_becomes_failure() -> None:
	req = EnvironmentVariableCheck(
		name="bad_env",
		env_var="BAD-NAME",
		expected="x",
		executor=_InvalidEnvVarExecutor(),
	)
	result = req.check()
	assert not result.ok
	assert "invalid environment variable name" in result.message


def test_benchmark_command_uses_runtime_executor(tmp_path: Path) -> None:
	executor = FakeRuntimeExecutor(
		proc_result=ProcResult(
			returncode=0,
			stdout="numpy ok\n",
			stderr="",
			timed_out=False,
		)
	)
	req = BenchmarkCommandCheck(
		name="python_numpy",
		cmd=("python3", "-c", "import numpy"),
		cwd=tmp_path,
		signature="numpy ok",
		use_shell=False,
		executor=executor,
	)
	assert req.check().ok
	call_names = [name for name, _payload in executor.calls]
	assert "path_exists" in call_names
	assert "path_is_dir" in call_names
	assert "run_process_capture" in call_names


def test_benchmark_command_local_env_overrides_preserve_host_env(tmp_path: Path) -> None:
	req = BenchmarkCommandCheck(
		name="local_env_merge",
		cmd=("python3", "-c", "import os; print(os.environ.get('FOO', ''))"),
		cwd=tmp_path,
		signature="bar",
		env_overrides={"FOO": "bar"},
		use_shell=False,
	)
	assert req.check().ok


def test_benchmark_command_passes_only_env_overrides(tmp_path: Path) -> None:
	executor = FakeRuntimeExecutor()
	req = BenchmarkCommandCheck(
		name="python_numpy",
		cmd=("python3", "-c", "import numpy"),
		cwd=tmp_path,
		signature=None,
		env_overrides={"FOO": "bar"},
		use_shell=False,
		executor=executor,
	)
	assert req.check().ok
	proc_calls = [(n, p) for n, p in executor.calls if n == "run_process_capture"]
	assert len(proc_calls) == 1
	_, payload = proc_calls[0]
	assert isinstance(payload, dict)
	assert payload["env"] == {"FOO": "bar"}


def test_build_command_uses_runtime_executor(tmp_path: Path) -> None:
	executor = FakeRuntimeExecutor(
		proc_result=ProcResult(
			returncode=0,
			stdout="build ok\n",
			stderr="",
			timed_out=False,
		)
	)
	req = BuildCommandCheck(
		name="build",
		cwd=tmp_path,
		cmd=("make", "all"),
		executor=executor,
	)
	assert req.check().ok
	call_names = [name for name, _payload in executor.calls]
	assert "path_exists" in call_names
	assert "path_is_dir" in call_names
	assert "run_process_capture" in call_names


def test_build_command_local_env_overrides_preserve_host_env(tmp_path: Path) -> None:
	req = BuildCommandCheck(
		name="local_build_env_merge",
		cwd=tmp_path,
		cmd=("python3", "-c", "import os; print(os.environ.get('CC', ''))"),
		env_overrides={"CC": "clang"},
	)
	result = req.check()
	assert result.ok
	assert "clang" in result.stdout


def test_build_command_passes_only_env_overrides(tmp_path: Path) -> None:
	executor = FakeRuntimeExecutor()
	req = BuildCommandCheck(
		name="build",
		cwd=tmp_path,
		cmd=("make", "all"),
		env_overrides={"CC": "clang"},
		executor=executor,
	)
	assert req.check().ok
	proc_calls = [(n, p) for n, p in executor.calls if n == "run_process_capture"]
	assert len(proc_calls) == 1
	_, payload = proc_calls[0]
	assert isinstance(payload, dict)
	assert payload["env"] == {"CC": "clang"}
