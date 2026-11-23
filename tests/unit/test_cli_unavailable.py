from __future__ import annotations

import pytest

from cli import cli_main

UNAVAILABLE = [
	pytest.param(["run"], "benchmark runner is unavailable in this checkout", id="run"),
	pytest.param(
		["case", "export"], "case export is unavailable in this checkout", id="case-export"
	),
	pytest.param(
		["case", "summarize"], "case summarize is unavailable in this checkout", id="case-summarize"
	),
	pytest.param(
		["runtime", "run"], "runtime run is unavailable in this checkout", id="runtime-run"
	),
]
JUNK = [[], ["--output", "x", "foo"], ["foo", "--input-file", "y", "--skip-incompatible"]]


@pytest.mark.parametrize("junk", JUNK, ids=["bare", "options-first", "positional-first"])
@pytest.mark.parametrize(("command", "message"), UNAVAILABLE)
def test_unavailable_command_reports_message_for_any_args(
	command: list[str], message: str, junk: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
	assert cli_main([*command, *junk]) == 1

	captured = capsys.readouterr()
	assert captured.err.strip() == message
	assert "usage:" not in captured.err
	assert captured.out == ""


@pytest.mark.parametrize(
	("parent", "name"),
	[([], "run"), (["case"], "export"), (["case"], "summarize"), (["runtime"], "run")],
)
def test_unavailable_command_stays_listed_in_help(
	parent: list[str], name: str, capsys: pytest.CaptureFixture[str]
) -> None:
	with pytest.raises(SystemExit) as exc:
		cli_main([*parent, "--help"])

	assert exc.value.code == 0
	lines = capsys.readouterr().out.splitlines()
	assert any(line.split()[:1] == [name] and "(unavailable)" in line for line in lines)


def test_available_command_still_rejects_unknown_args(capsys: pytest.CaptureFixture[str]) -> None:
	with pytest.raises(SystemExit) as exc:
		cli_main(["case", "validate", "some_case", "--bogus"])

	assert exc.value.code == 2
	assert "unrecognized arguments: --bogus" in capsys.readouterr().err
