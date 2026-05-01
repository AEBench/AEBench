"""Oracle check primitive tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from evaluator.oracles.checks import CommandCheck, EnvVarCheck, PathCheck, VersionCheck
from evaluator.oracles.utils import CheckResult, ProcResult, RuntimeCheckExecutor


class FakeRuntimeExecutor(RuntimeCheckExecutor):
    path_separator = ":"

    def __init__(self, *, resolved=None, env=None, proc_result=None):
        self.resolved = resolved
        self.env = dict(env or {})
        self.proc_result = proc_result or ProcResult(returncode=0, stdout="", stderr="", timed_out=False)
        self.calls = []

    def resolve_executable(self, executable: str, *, env: Mapping[str, str] | None = None):
        self.calls.append(("resolve_executable", executable))
        return self.resolved

    def read_env_var(self, name: str, *, env: Mapping[str, str] | None = None):
        self.calls.append(("read_env_var", name))
        return dict(env or {}).get(name, self.env.get(name))

    def run_process_capture(
        self, *, cmd: str | Sequence[str], cwd: Path | None, env: Mapping[str, str] | None,
        timeout_seconds: float, use_shell: bool = False, capture_limit_chars: int = 16384,
        drain_after_kill: bool = False, encoding: str | None = None,
        on_chunk: Callable[[str, str], None] | None = None,
    ):
        self.calls.append(("run_process_capture", {"cmd": cmd, "cwd": cwd, "env": dict(env or {})}))
        if on_chunk and self.proc_result.stdout:
            on_chunk("stdout", self.proc_result.stdout)
        return self.proc_result

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
        return path.read_text(encoding=encoding)

    def close(self) -> None:
        pass


def test_check_result_helpers() -> None:
    assert CheckResult.success("ok").ok
    assert not CheckResult.failure("bad").ok
    assert CheckResult.failure("bad", returncode=2).returncode == 2


def test_path_check(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    d = tmp_path / "dir"
    f.write_text("x")
    d.mkdir()

    assert PathCheck(name="any", path=f).check().ok
    assert PathCheck(name="file", path=f, kind="file").check().ok
    assert PathCheck(name="dir", path=d, kind="dir").check().ok
    assert not PathCheck(name="missing", path=tmp_path / "missing").check().ok


def test_path_check_uses_executor(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    executor = FakeRuntimeExecutor()

    assert PathCheck(name="file", path=f, kind="file", executor=executor).check().ok
    assert [name for name, _ in executor.calls] == ["path_exists", "path_is_file"]


def test_version_check_uses_executor() -> None:
    executor = FakeRuntimeExecutor(
        resolved="/usr/bin/python3",
        proc_result=ProcResult(returncode=0, stdout="Python 3.11.9\n", stderr="", timed_out=False),
    )

    result = VersionCheck(
        name="python",
        cmd=("python3", "--version"),
        min_version=(3, 11, 0),
        executor=executor,
    ).check()

    assert result.ok
    assert [name for name, _ in executor.calls] == ["resolve_executable", "run_process_capture"]


def test_version_check_fails_for_high_min_version() -> None:
    result = VersionCheck(
        name="python",
        cmd=("python3", "--version"),
        min_version=(99, 0, 0),
    ).check()

    assert not result.ok


def test_env_var_check() -> None:
    executor = FakeRuntimeExecutor(env={"AEBENCH_TEST": "hello-world"})

    assert EnvVarCheck(
        name="contains",
        env_var="AEBENCH_TEST",
        expected="world",
        match_mode="contains",
        executor=executor,
    ).check().ok


def test_env_var_missing_fails() -> None:
    result = EnvVarCheck(
        name="missing",
        env_var="AEBENCH_MISSING",
        expected="x",
        executor=FakeRuntimeExecutor(),
    ).check()

    assert not result.ok


def test_command_check_uses_executor(tmp_path: Path) -> None:
    executor = FakeRuntimeExecutor(
        proc_result=ProcResult(returncode=0, stdout="build ok\n", stderr="", timed_out=False)
    )

    result = CommandCheck(
        name="command",
        cmd=("echo", "build ok"),
        cwd=tmp_path,
        signature="build ok",
        executor=executor,
    ).check()

    assert result.ok
    assert [name for name, _ in executor.calls] == ["path_exists", "path_is_dir", "run_process_capture"]


def test_command_check_env_is_passed(tmp_path: Path) -> None:
    executor = FakeRuntimeExecutor()

    assert CommandCheck(
        name="env",
        cmd=("env",),
        cwd=tmp_path,
        env={"FOO": "bar"},
        executor=executor,
    ).check().ok

    proc_payloads = [payload for name, payload in executor.calls if name == "run_process_capture"]
    assert proc_payloads[0]["env"] == {"FOO": "bar"}
