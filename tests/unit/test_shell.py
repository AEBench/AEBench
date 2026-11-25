from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import shell
from cli import cli_main


class _Exec(Exception):
	pass


def _fake_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, running: bool) -> Path:
	"""Puts a tmux on PATH that records its arguments and prints a pane id."""
	calls = tmp_path / "tmux_calls"
	bin_dir = tmp_path / "bin"
	bin_dir.mkdir()
	script = bin_dir / "tmux"
	script.write_text(
		"#!/bin/sh\n"
		f'echo "$*" >> {calls}\n'
		f'[ "$1" = has-session ] && exit {0 if running else 1}\n'
		'n=$(wc -l < "' + str(calls) + '"); echo "%$n"\n'
	)
	script.chmod(0o755)
	monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
	monkeypatch.delenv("TMUX", raising=False)

	def fake_exec(_file: str, argv: list[str]) -> None:
		calls.open("a").write(" ".join(argv[1:]) + "\n")
		raise _Exec

	monkeypatch.setattr(shell.os, "execvp", fake_exec)
	return calls


def test_launch_shell_puts_the_agent_on_the_left_output_on_the_right_and_prompt_below(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	calls = _fake_tmux(tmp_path, monkeypatch, running=False)
	(tmp_path / "session").mkdir()
	(tmp_path / "session" / shell.OUTPUT_LOG).write_text("old output\n")

	with pytest.raises(_Exec):
		shell.launch_shell(tmp_path / "session")

	lines = calls.read_text().splitlines()
	new, agent, output = lines[1], lines[2], lines[3]
	assert "-d -s aebench" in new and "shell " in new
	# The agent pane is split off above the prompt (%2), then output goes to its right.
	assert "-v -b" in agent and "-t %2" in agent and "cli trace follow" in agent
	assert "-h -t %3" in output and "tail -n +1 -F" in output
	assert lines[-1] == "attach-session -t aebench"
	assert (tmp_path / "session" / shell.OUTPUT_LOG).read_text() == ""


def test_launch_shell_reattaches_to_a_running_session(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	calls = _fake_tmux(tmp_path, monkeypatch, running=True)

	with pytest.raises(_Exec):
		shell.launch_shell(tmp_path / "session")

	assert calls.read_text().splitlines() == ["has-session -t aebench", "attach-session -t aebench"]


def test_launch_shell_without_tmux_says_to_install_it(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
	monkeypatch.setattr(shell.shutil, "which", lambda _name: None)

	assert shell.launch_shell(tmp_path) == 1
	assert "install tmux" in capsys.readouterr().err


def _typed(monkeypatch: pytest.MonkeyPatch, *lines: str | type[BaseException]) -> None:
	pending = list(lines)

	def fake_input(_prompt: str) -> str:
		if not pending:
			raise EOFError
		line = pending.pop(0)
		if isinstance(line, type):
			raise line
		return line

	monkeypatch.setattr("builtins.input", fake_input)


def _echo_cli(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
	monkeypatch.setattr(shell, "_AEBENCH", [sys.executable, "-c", code])


def _log_once(session_dir: Path, done: Callable[[str], bool]) -> str:
	deadline = time.monotonic() + 10
	while not done(text := (session_dir / shell.OUTPUT_LOG).read_text()):
		assert time.monotonic() < deadline, text
		time.sleep(0.05)
	return text


def test_repl_runs_each_command_and_writes_its_output_to_the_log(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	# One write per line, so lines of commands running together do not mix.
	_echo_cli(
		monkeypatch, "import os, sys; os.write(1, f'args {sys.argv[1:]}\\n'.encode()); sys.exit(3)"
	)
	_typed(monkeypatch, "agent list", "", "aebench case run 'my case'", "exit")

	assert shell.repl(tmp_path) == 0

	log = _log_once(tmp_path, lambda text: text.count("[exit 3]") == 2)
	lines = log.splitlines()
	# Commands run at the same time, so their lines may interleave.
	assert "$ aebench agent list" in lines and "args ['agent', 'list']" in lines
	# A leading "aebench" is dropped, and quotes group words as in a shell.
	assert "$ aebench case run 'my case'" in lines
	assert "args ['case', 'run', 'my case']" in lines
	assert (tmp_path / shell.HISTORY).is_file()


def test_repl_keeps_going_while_a_command_runs_and_ctrl_c_interrupts_it(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	_echo_cli(monkeypatch, "import time; time.sleep(30)")
	sent: list[tuple[int, int]] = []
	monkeypatch.setattr(shell.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
	_typed(monkeypatch, "case run x", "exit", KeyboardInterrupt)

	shell.repl(tmp_path)

	out = capsys.readouterr().out
	# Exit is refused while the run goes on; Ctrl-C reaches the run, not the prompt.
	assert "still running" in out
	assert len(sent) == 1 and sent[0][1] == signal.SIGINT
	os.killpg(sent[0][0], signal.SIGKILL)


def test_repl_reports_a_line_it_cannot_split(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	_typed(monkeypatch, "case run 'unclosed")

	shell.repl(tmp_path)

	assert "quotation" in capsys.readouterr().out
	assert not (tmp_path / shell.OUTPUT_LOG).exists()


def test_cli_shell_opens_the_session_under_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
	opened: list[Path] = []
	monkeypatch.setattr(shell, "launch_shell", lambda session_dir: opened.append(session_dir) or 0)

	assert cli_main(["shell"]) == 0
	assert opened == [Path("~/.cache/aebench/shell").expanduser()]
