"""What the shim must report to the broker for one ordinary command.

The command is ``cat``: it proves stdin reaches the child, that what the child
wrote comes back to the agent unchanged, and that the broker is told about all
of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import ShimRunner
from .fake_broker import FakeBroker

pytestmark = pytest.mark.shim

STDIN_TEXT = "hello from stdin\n"

#: Written into the child's environment to prove only names are reported.
SECRET_NAME = "AEBENCH_TEST_SECRET"
SECRET_VALUE = "sk-value-that-must-never-be-reported"


def test_cat_reports_every_expected_message(
	shim: Path, broker: FakeBroker, run_shim: ShimRunner, tmp_path: Path
) -> None:
	workdir = tmp_path / "workspace"
	workdir.mkdir()

	result = run_shim(
		["-c", "cat"],
		input=STDIN_TEXT,
		cwd=workdir,
		env={SECRET_NAME: SECRET_VALUE},
	)

	# What the agent saw: stdin reached cat, its output came back untouched.
	assert result.returncode == 0
	assert result.stdout == STDIN_TEXT
	assert result.stderr == ""

	session = broker.wait_for_session()
	assert session.error is None

	# The full conversation. cat writes nothing to stderr, and an empty stream
	# is sent as no frame at all rather than an empty one.
	assert session.kind_names == ["command_info", "stdout", "end"]

	info = session.command_info
	assert info["argv"] == [str(shim), "-c", "cat"]
	assert info["cwd"] == str(workdir)
	assert isinstance(info["pid"], int) and info["pid"] > 0
	assert SECRET_NAME in info["env_keys"]
	assert SECRET_VALUE not in json.dumps(info)

	assert session.stdout == STDIN_TEXT.encode()
	assert session.stderr == b""
	assert session.end == {"exit_code": 0, "signal": None}
