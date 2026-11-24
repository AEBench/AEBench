from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

import trace_viewer
from cli import cli_main
from project_config import load_project_config
from trace_viewer import follow, follow_latest, follow_trace

from .test_trace_viewer import _DSH_LOG, _ZCODE_LOG

_CLAUDE_LINES = (
	# Claude may report its rate limit before the session starts.
	'[2026-11-21T09:11:46Z] {"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}\n'
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


def test_follow_trace_reads_dsh_runs_with_the_dsh_parser(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("TEST_TOKEN", "secret-value")
	monkeypatch.setenv("COLUMNS", "120")

	follow_trace(_finished_run(tmp_path, _DSH_LOG), poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == [
		"          out    [llm-deepseek-api-key] model listing loaded 2 model(s)",
		"01:00:03  text   Inspecting the artifact.",
		"01:00:04  Bash   echo [REDACTED:TEST_TOKEN]",
		"01:00:06  read   missing.txt",
		"01:00:07  error  no such file",
		"01:00:09  done   finished, 1 turns",
	]


def test_follow_trace_reads_zcode_runs_with_the_zcode_parser(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("TEST_TOKEN", "secret-value")
	monkeypatch.setenv("COLUMNS", "120")

	follow_trace(_finished_run(tmp_path, _ZCODE_LOG), poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == [
		"02:00:00  out    ZCode Built-in skipped (not-due)",
		"          text   Inspecting.",
		"          Bash   echo [REDACTED:TEST_TOKEN]",
		"          WebFetch u",
		"          error  TOOL_EXECUTION_FAILED: Unsupported content type",
		"          text   All done.",
		"          done   success, 2 turns",
	]


def test_follow_trace_uses_the_recorded_trace_format(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")
	run_dir = _finished_run(tmp_path, '{"type":"text","text":"hello"}\n')
	(run_dir / "result.jsonl").write_text('{"trace_format":"raw"}\n', encoding="utf-8")

	follow_trace(run_dir, poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == [
		'          out    {"type":"text","text":"hello"}'
	]


def test_follow_trace_prints_logs_of_unknown_formats_raw(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")
	log = 'starting\n{"kind":"step","n":1}\n{"type":"progress","n":2}\n'

	follow_trace(_finished_run(tmp_path, log), poll_seconds=0.01)

	assert capsys.readouterr().out.splitlines() == [
		"          out    starting",
		'          out    {"kind":"step","n":1}',
		'          out    {"type":"progress","n":2}',
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


class _Stopped(Exception):
	pass


def _follow_latest_in_thread(
	runs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[threading.Thread, Callable[[], None]]:
	"""Starts follow_latest, which never returns, and a function that stops it."""
	stop = threading.Event()

	def sleep(seconds: float) -> None:
		if stop.is_set():
			raise _Stopped
		time.sleep(seconds)

	monkeypatch.setattr(follow, "time", SimpleNamespace(sleep=sleep))

	def run() -> None:
		try:
			follow_latest(runs_dir, poll_seconds=0.01)
		except _Stopped:
			pass

	thread = threading.Thread(target=run)
	thread.start()

	def stopper() -> None:
		stop.set()
		thread.join(timeout=5)

	return thread, stopper


def _start_run(runs_dir: Path, case: str, run_id: str, text: str) -> Path:
	run_dir = runs_dir / case / run_id
	run_dir.mkdir(parents=True)
	(run_dir / "runner_output.log").write_text(f"[2026-11-24T10:00:00Z] {text}\n", encoding="utf-8")
	return run_dir


def test_follow_latest_skips_runs_that_finished_before_it_started(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")
	old = _start_run(tmp_path, "case_a", "2026-11-24_09-00-00_000000", "old run")
	(old / "case_result.json").write_text("{}", encoding="utf-8")
	(tmp_path / "case_a" / "monitor").mkdir()
	thread, stop = _follow_latest_in_thread(tmp_path, monkeypatch)

	time.sleep(0.1)
	_start_run(tmp_path, "case_b", "2026-11-24_10-00-00_000000", "new run")
	time.sleep(0.2)
	stop()

	assert not thread.is_alive()
	captured = capsys.readouterr()
	assert captured.out.splitlines() == [
		"=== case_b/2026-11-24_10-00-00_000000",
		"10:00:00  out    new run",
	]
	assert "Waiting for a run" in captured.err


def test_follow_latest_prints_each_run_under_a_header(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")
	thread, stop = _follow_latest_in_thread(tmp_path, monkeypatch)

	first = _start_run(tmp_path, "case_b", "2026-11-24_10-00-00_000000", "first run")
	time.sleep(0.1)
	(first / "case_result.json").write_text("{}", encoding="utf-8")
	time.sleep(0.1)
	_start_run(tmp_path, "case_a", "2026-11-24_11-00-00_000000", "second run")
	time.sleep(0.2)
	stop()

	assert not thread.is_alive()
	assert capsys.readouterr().out.splitlines() == [
		"=== case_b/2026-11-24_10-00-00_000000",
		"10:00:00  out    first run",
		"=== case_a/2026-11-24_11-00-00_000000",
		"10:00:00  out    second run",
	]


def test_follow_latest_moves_to_a_newer_run_when_one_starts(
	tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
	monkeypatch.setenv("COLUMNS", "120")
	# A run that stopped without a case result, e.g. after a crash.
	_start_run(tmp_path, "case_a", "2026-11-24_09-00-00_000000", "stuck run")
	thread, stop = _follow_latest_in_thread(tmp_path, monkeypatch)

	time.sleep(0.1)
	_start_run(tmp_path, "case_b", "2026-11-24_10-00-00_000000", "new run")
	time.sleep(0.2)
	stop()

	assert not thread.is_alive()
	assert capsys.readouterr().out.splitlines() == [
		"=== case_a/2026-11-24_09-00-00_000000",
		"10:00:00  out    stuck run",
		"=== case_b/2026-11-24_10-00-00_000000",
		"10:00:00  out    new run",
	]


def test_trace_follow_without_a_run_dir_follows_the_workspace_runs(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	followed: list[Path] = []
	monkeypatch.setattr(trace_viewer, "follow_latest", followed.append)

	assert cli_main(["trace", "follow"]) == 0

	state = load_project_config(Path.cwd())
	assert followed == [state.config.resolve_case_runs_dir(state.root)]
