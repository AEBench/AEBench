"""Oracle internal check logic tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	FilesystemPathCheck,
	PathType,
	VersionCompare,
)
from evaluator.oracles.utils import CheckResult, CheckOutcome



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
