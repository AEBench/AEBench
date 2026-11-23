from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cli import cli_main
from trace_viewer import follow_trace

_CLAUDE_LINES = (
	'[2026-11-21T09:11:47Z] {"type":"system","subtype":"init","cwd":"/repo","session_id":"s1"}\n'
	'[2026-11-21T09:11:50Z] {"type":"assistant","session_id":"s1","message":{"content":['
	'{"type":"thinking","thinking":""},'
	'{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"pip install\\n -r requirements.txt",'
	'"description":"Install"}}]}}\n'
	'[2026-11-21T09:11:50Z] {"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}\n'
	'[2026-11-21T09:11:51Z] {"type":"system","subtype":"thinking_tokens","tokens":12}\n'
	'[2026-11-21T09:11:52Z] {"type":"user","session_id":"s1","message":{"content":['
	'{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}\n'
	'[2026-11-21T09:12:03Z] {"type":"assistant","session_id":"s1","message":{"content":['
	'{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"results/foo.py",'
	'"old_string":"a","new_string":"b"}}]}}\n'
	'[2026-11-21T09:12:04Z] {"type":"user","session_id":"s1","message":{"content":['
	'{"type":"tool_result","tool_use_id":"t2","is_error":true,"content":"File not found"}]}}\n'
	'[2026-11-21T09:12:10Z] {"type":"assistant","session_id":"s1","message":{"content":['
	'{"type":"text","text":"I\'ll now run\\nfigure 8 with key secret-value"}]}}\n'
	'[2026-11-21T09:20:00Z] {"type":"result","subtype":"success","num_turns":3,"session_id":"s1"}\n'
)

_CODEX_LINES = (
	"[2026-10-23T02:38:39Z] Reading prompt from stdin...\n"
	'[2026-10-23T02:38:40Z] {"type":"thread.started","thread_id":"th1"}\n'
	'[2026-10-23T02:38:40Z] {"type":"turn.started"}\n'
	'[2026-10-23T02:38:44Z] {"type":"item.completed","item":{"id":"i0","type":"reasoning",'
	'"text":"**Inspecting**"}}\n'
	'[2026-10-23T02:38:45Z] {"type":"item.completed","item":{"id":"i1","type":"agent_message",'
	'"text":"I will inspect the repo."}}\n'
	'[2026-10-23T02:38:46Z] {"type":"item.started","item":{"id":"i2","type":"command_execution",'
	'"command":"/bin/bash -lc \\"ls -la\\"","status":"in_progress"}}\n'
	'[2026-10-23T02:38:47Z] {"type":"item.completed","item":{"id":"i2","type":"command_execution",'
	'"command":"/bin/bash -lc \\"ls -la\\"","aggregated_output":"x","exit_code":0}}\n'
	'[2026-10-23T02:38:48Z] {"type":"item.started","item":{"id":"i3","type":"command_execution",'
	'"command":"make","status":"in_progress"}}\n'
	'[2026-10-23T02:38:49Z] {"type":"item.completed","item":{"id":"i3","type":"command_execution",'
	'"command":"make","aggregated_output":"boom","exit_code":2}}\n'
	'[2026-10-23T02:38:50Z] {"type":"item.started","item":{"id":"i4","type":"file_change",'
	'"changes":[{"path":"/repo/a.py","kind":"update"}],"status":"in_progress"}}\n'
	'[2026-10-23T02:38:51Z] {"type":"item.completed","item":{"id":"i4","type":"file_change",'
	'"changes":[{"path":"/repo/a.py","kind":"update"},{"path":"/repo/b.py","kind":"add"}]}}\n'
	'[2026-10-23T02:38:52Z] {"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
	"not json and no timestamp\n"
)


def _finished_run(tmp_path: Path, log: str) -> Path:
	(tmp_path / "runner_output.log").write_text(log, encoding="utf-8")
	(tmp_path / "case_result.json").write_text("{}", encoding="utf-8")
	return tmp_path


def test_follow_trace_curates_claude_events(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("TEST_TOKEN", "secret-value")
	monkeypatch.setenv("COLUMNS", "120")

	follow_trace(_finished_run(tmp_path, _CLAUDE_LINES), poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == [
		"09:11:50  Bash   pip install -r requirements.txt",
		"09:12:03  Edit   results/foo.py",
		"09:12:04  error  File not found",
		"09:12:10  text   I'll now run figure 8 with key [REDACTED:TEST_TOKEN]",
		"09:20:00  done   success, 3 turns",
	]


def test_follow_trace_curates_codex_events_and_prints_other_lines_raw(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")

	follow_trace(_finished_run(tmp_path, _CODEX_LINES), poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == [
		"02:38:39  out    Reading prompt from stdin...",
		"02:38:45  text   I will inspect the repo.",
		"02:38:46  shell  ls -la",
		"02:38:48  shell  make",
		"02:38:49  error  exit 2: make",
		"02:38:51  edit   /repo/a.py, /repo/b.py",
		"          out    not json and no timestamp",
	]


def test_follow_trace_truncates_to_the_terminal_width(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "30")
	log = '[2026-10-23T02:38:46Z] {"type":"item.completed","item":{"type":"agent_message","text":"%s"}}\n'

	follow_trace(_finished_run(tmp_path, log % ("word " * 20)), poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == ["02:38:46  text   word word wor"]


def test_follow_trace_waits_for_the_log_and_follows_appended_lines(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")
	thread = threading.Thread(target=follow_trace, args=(tmp_path,), kwargs={"poll_seconds": 0.01})
	thread.start()
	time.sleep(0.05)
	log = tmp_path / "runner_output.log"
	with log.open("w", encoding="utf-8") as handle:
		handle.write("[2026-10-23T02:38:39Z] first\n[2026-10-23T02:38:40Z] sec")
		handle.flush()
		time.sleep(0.05)
		handle.write("ond\n")
		handle.flush()
		time.sleep(0.05)
		handle.write("[2026-10-23T02:38:41Z] last\n")
	(tmp_path / "case_result.json").write_text("{}", encoding="utf-8")
	thread.join(timeout=5)

	assert not thread.is_alive()
	captured = capsys.readouterr()
	assert "Waiting for" in captured.err
	assert captured.out.splitlines() == [
		"02:38:39  out    first",
		"02:38:40  out    second",
		"02:38:41  out    last",
	]


def test_trace_follow_command_prints_a_finished_run(
	tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
	run_dir = _finished_run(tmp_path, "[2026-10-23T02:38:39Z] hello\n")

	assert cli_main(["trace", "follow", str(run_dir)]) == 0
	assert capsys.readouterr().out.splitlines() == ["02:38:39  out    hello"]
