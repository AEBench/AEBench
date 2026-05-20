from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import checks
from evaluator.oracles.utils import ProcResult


@dataclass
class FakeExecutor:
    path_separator: str = ':'
    resolved: str | None = '/bin/fake'
    env: dict[str, str] | None = None
    exists: bool = True
    is_file: bool = True
    is_dir: bool = False
    stdout: str = ''
    stderr: str = ''
    returncode: int = 0

    def resolve_executable(self, executable: str, *, env=None):
        return self.resolved

    def read_env_var(self, name: str, *, env=None):
        if self.env is None:
            return None
        return self.env.get(name)

    def run_process_capture(self, *, cmd, cwd, env, timeout_seconds, use_shell=False, capture_limit_chars=16384, drain_after_kill=False, encoding=None, on_chunk=None):
        return ProcResult(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr, timed_out=False)

    def path_exists(self, path):
        return self.exists

    def path_is_file(self, path):
        return self.is_file

    def path_is_dir(self, path):
        return self.is_dir

    def read_file_text(self, path, encoding='utf-8'):
        return 'content'

    def close(self):
        return None


def test_version_check_success() -> None:
    executor = FakeExecutor(stdout='tool version 1.2.3')
    chk = checks.VersionCheck(name='tool', cmd=['tool'], min_version=(1, 2, 0), executor=executor)
    result = chk.check()
    assert result.ok is True


def test_version_check_failure_for_low_version() -> None:
    executor = FakeExecutor(stdout='tool version 1.1.9')
    chk = checks.VersionCheck(name='tool', cmd=['tool'], min_version=(1, 2, 0), executor=executor)
    result = chk.check()
    assert result.ok is False
    assert 'does not satisfy' in result.message


def test_env_var_check_exact_match() -> None:
    executor = FakeExecutor(env={'EGWALKER_HOME': '/tmp/egwalker'})
    chk = checks.EnvVarCheck(name='env', env_var='EGWALKER_HOME', expected='/tmp/egwalker', executor=executor)
    result = chk.check()
    assert result.ok is True


def test_env_var_check_contains_match() -> None:
    executor = FakeExecutor(env={'EGWALKER_HOME': '/opt/egwalker/bin'})
    chk = checks.EnvVarCheck(name='env', env_var='EGWALKER_HOME', expected='egwalker', match_mode=checks.EnvMatchMode.CONTAINS, executor=executor)
    result = chk.check()
    assert result.ok is True


def test_path_check_file_and_directory() -> None:
    file_exec = FakeExecutor(exists=True, is_file=True, is_dir=False)
    dir_exec = FakeExecutor(exists=True, is_file=False, is_dir=True)

    file_check = checks.PathCheck(name='file', path=Path('README.md'), kind=checks.PathKind.FILE, executor=file_exec)
    dir_check = checks.PathCheck(name='dir', path=Path('.'), kind=checks.PathKind.DIRECTORY, executor=dir_exec)

    assert file_check.check().ok is True
    assert dir_check.check().ok is True


def test_path_check_reports_missing_path() -> None:
    executor = FakeExecutor(exists=False, is_file=False, is_dir=False)
    chk = checks.PathCheck(name='path', path=Path('missing.txt'), kind=checks.PathKind.FILE, executor=executor)
    result = chk.check()
    assert result.ok is False
    assert 'not found' in result.message
