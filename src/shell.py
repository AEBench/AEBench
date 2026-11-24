"""Opens AEBench in tmux: the agent's live trace, AEBench's output, and a prompt.

+----------------------------+----------------------------+
| agent: aebench trace follow| output: AEBench's prints   |
+----------------------------+----------------------------+
| aebench> commands typed here                            |
+---------------------------------------------------------+
"""

from __future__ import annotations

import os
import readline
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO

SESSION = "aebench"
OUTPUT_LOG = "output.log"
HISTORY = "history"
_AEBENCH = [sys.executable, "-m", "cli"]


def launch_shell(session_dir: Path) -> int:
	"""Attaches to the aebench tmux session, creating it first if needed."""
	if shutil.which("tmux") is None:
		print("aebench shell runs in tmux; install tmux and try again.", file=sys.stderr)
		return 1
	if subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True).returncode:
		_create_session(session_dir)
	# Inside tmux, switch to the session instead of nesting one.
	verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
	os.execvp("tmux", ["tmux", verb, "-t", SESSION])
	return 0


def _create_session(session_dir: Path) -> None:
	session_dir.mkdir(parents=True, exist_ok=True)
	log = session_dir / OUTPUT_LOG
	log.write_text("")
	cwd = str(Path.cwd())
	prompt = _tmux(
		"new-session",
		"-d",
		"-s",
		SESSION,
		"-x",
		"200",
		"-y",
		"50",
		"-c",
		cwd,
		shlex.join([sys.executable, "-m", "shell", str(session_dir)]),
	)
	agent = _tmux(
		"split-window",
		"-v",
		"-b",
		"-l",
		"75%",
		"-t",
		prompt,
		"-c",
		cwd,
		shlex.join([*_AEBENCH, "trace", "follow"]),
	)
	output = _tmux("split-window", "-h", "-t", agent, f"tail -n +1 -F {shlex.quote(str(log))}")
	for pane, title in ((agent, "agent"), (output, "aebench output"), (prompt, "commands")):
		_tmux("select-pane", "-t", pane, "-T", title)
	_tmux("set-option", "-t", SESSION, "pane-border-status", "top")
	_tmux("set-option", "-t", SESSION, "pane-border-format", " #{pane_title} ")
	_tmux("set-option", "-t", SESSION, "mouse", "on")


def _tmux(*args: str) -> str:
	"""Runs a tmux command; for a new pane, returns its id."""
	if args[0] in ("new-session", "split-window"):
		args = (args[0], "-P", "-F", "#{pane_id}", *args[1:])
	return subprocess.run(
		["tmux", *args], check=True, capture_output=True, text=True
	).stdout.strip()


def repl(session_dir: Path) -> int:
	"""Reads AEBench commands and runs each one, its output going to the log.

	The prompt comes back at once, so a long run does not block other commands.
	Ctrl-C interrupts the newest command still running.
	"""
	log_path, history = session_dir / OUTPUT_LOG, session_dir / HISTORY
	try:
		readline.read_history_file(history)
	except OSError:
		pass
	running: subprocess.Popen[bytes] | None = None
	while True:
		try:
			line = input("aebench> ")
		except EOFError:
			break
		except KeyboardInterrupt:
			print()
			if running is not None and running.poll() is None:
				os.killpg(running.pid, signal.SIGINT)
				print("Interrupt sent to the running command.")
			continue
		try:
			args = shlex.split(line)
		except ValueError as error:
			print(error)
			continue
		args = args[1:] if args[:1] == ["aebench"] else args
		if args in (["exit"], ["quit"]):
			if running is not None and running.poll() is None:
				print("A command is still running: Ctrl-C stops it, Ctrl-b d detaches.")
				continue
			break
		if args:
			running = _start(args, log_path)
	readline.write_history_file(history)
	return 0


def _start(args: list[str], log_path: Path) -> subprocess.Popen[bytes]:
	log = log_path.open("a", encoding="utf-8")
	log.write(f"\n$ aebench {shlex.join(args)}\n")
	log.flush()
	# Its own session, so Ctrl-C at the prompt reaches it only when forwarded.
	process = subprocess.Popen(
		[*_AEBENCH, *args],
		stdin=subprocess.DEVNULL,
		stdout=log,
		stderr=subprocess.STDOUT,
		start_new_session=True,
		env={**os.environ, "PYTHONUNBUFFERED": "1"},
	)
	threading.Thread(target=_write_exit, args=(process, log), daemon=True).start()
	return process


def _write_exit(process: subprocess.Popen[bytes], log: IO[str]) -> None:
	with log:
		log.write(f"[exit {process.wait()}]\n")


if __name__ == "__main__":
	sys.exit(repl(Path(sys.argv[1])))
