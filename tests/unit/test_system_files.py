"""Recording the system files the agent's container changed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from runtime import monitoring
from runtime.monitoring import SYSTEM_FILES_DIFF_BASENAME, check_system_files

_DOCKER_DIFF = """\
C /usr
C /usr/bin
C /usr/bin/bash
C /usr/bin/cat
A /usr/local/bin/python3
C /usr/lib/python3/dist-packages
A /usr/lib/python3/dist-packages/aebench.pth
A /usr/local/lib/python3.10/site-packages/sitecustomize.py
C /etc
A /etc/ld.so.preload
C /root/.bash_history
A /tmp/scratch.txt
"""


def _fake_docker(
	monkeypatch: pytest.MonkeyPatch, stdout: str, returncode: int = 0
) -> list[list[str]]:
	calls: list[list[str]] = []

	def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
		calls.append(cmd)
		return subprocess.CompletedProcess(
			cmd, returncode, stdout=stdout, stderr="no such container"
		)

	monkeypatch.setattr(monitoring.subprocess, "run", run)
	return calls


def test_changed_executables_startup_hooks_and_etc_are_recorded(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	calls = _fake_docker(monkeypatch, _DOCKER_DIFF)

	check = check_system_files("abc123", tmp_path)

	assert calls == [["docker", "diff", "abc123"]]
	assert check is not None
	assert check.changed == 12
	assert (check.executables_count, check.python_startup_count, check.etc_count) == (2, 2, 1)
	diff = json.loads((tmp_path / SYSTEM_FILES_DIFF_BASENAME).read_text(encoding="utf-8"))
	# Directories and the shim's own bash are left out; so is everything else.
	assert diff == {
		"changed": 12,
		"executables": ["C /usr/bin/cat", "A /usr/local/bin/python3"],
		"python_startup": [
			"A /usr/lib/python3/dist-packages/aebench.pth",
			"A /usr/local/lib/python3.10/site-packages/sitecustomize.py",
		],
		"etc": ["A /etc/ld.so.preload"],
	}


def test_an_unchanged_container_reports_nothing(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	_fake_docker(monkeypatch, "C /usr\nC /usr/bin\nC /usr/bin/bash\n")

	check = check_system_files("abc123", tmp_path)

	assert check is not None
	assert (check.executables_count, check.python_startup_count, check.etc_count) == (0, 0, 0)


def test_a_failed_docker_diff_records_nothing(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	_fake_docker(monkeypatch, "", returncode=1)

	assert check_system_files("gone", tmp_path) is None
	assert not (tmp_path / SYSTEM_FILES_DIFF_BASENAME).exists()
