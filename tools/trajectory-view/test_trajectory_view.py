"""Tests that trajectory_view prints Claude Code and Codex traces in each layout."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import trajectory_view  # noqa: E402


def log_line(ts, payload):
	return f"[2026-11-25T{ts}Z] {json.dumps(payload)}"


def assistant(ts, *blocks):
	return log_line(ts, {"type": "assistant", "timestamp": f"2026-11-25T{ts}.000Z", "message": {"content": list(blocks)}})


def user(ts, *blocks):
	return log_line(ts, {"type": "user", "timestamp": f"2026-11-25T{ts}.400Z", "message": {"content": list(blocks)}})


CLAUDE = [
	log_line("03:18:41", {"type": "system", "subtype": "init"}),
	log_line("03:18:42", {"type": "system", "subtype": "thinking_tokens"}),
	log_line("03:18:42", {"type": "rate_limit_event"}),
	assistant("03:18:53", {"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "Installing .NET 6 SDK."}),
	assistant("03:19:05", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make\n&& echo done", "description": "Build Dafny"}}),
	user("03:19:05", {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "\n".join(f"line {i}" for i in range(8))}),
	assistant("03:19:10", {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/repo/a.py", "old_string": "x = 1\ny = 2", "new_string": "x = 3"}}),
	user("03:19:11", {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "edited"}]}),
	log_line("03:27:40", {"type": "result", "subtype": "success", "num_turns": 17}),
]

CODEX = [
	"[2026-10-23T02:01:05Z] Reading prompt from stdin...",
	log_line("02:01:07", {"type": "thread.started", "thread_id": "abc"}),
	log_line("02:01:11", {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "hidden"}}),
	log_line("02:01:12", {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "/bin/bash -lc 'ls /repo'"}}),
	log_line("02:01:14", {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "/bin/bash -lc 'ls /repo'", "aggregated_output": "nope\n", "exit_code": 2}}),
	log_line("02:01:15", {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "Done."}}),
]


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
	monkeypatch.setenv("COLUMNS", "200")

	def run(lines, mode):
		log = tmp_path / "runner_output.log"
		log.write_text("\n".join(lines) + "\n")
		trajectory_view.main([str(log), f"--{mode}"])
		return capsys.readouterr().out.splitlines()

	return run


def test_short_prints_one_line_per_event_like_trace_follow(run):
	assert run(CLAUDE, "short") == [
		"03:18:53  text   Installing .NET 6 SDK.",
		"03:19:05  Bash   make && echo done",
		"03:19:05  error  line 0 line 1 line 2 line 3 line 4 line 5 line 6 line 7",
		"03:19:10  Edit   /repo/a.py",
		"03:27:40  done   success, 17 turns",
	]


def test_short_cuts_lines_to_the_terminal_width(run, monkeypatch):
	monkeypatch.setenv("COLUMNS", "30")
	assert run(CLAUDE, "short")[1] == "03:19:05  Bash   make && ec..."


def test_expanded_shows_text_and_failed_output_as_blocks(run):
	assert run(CLAUDE, "expanded") == [
		"03:18:53  text",
		"\tInstalling .NET 6 SDK.",
		"03:19:05  Bash   make && echo done",
		"03:19:05  out ✗",
		*[f"\tline {i}" for i in range(5)],
		"\t... 3 more lines",
		"03:19:10  Edit   /repo/a.py",
		"03:19:11  out    edited",
		"03:27:40  done   success, 17 turns",
	]


def test_verbose_joins_each_call_with_its_result(run):
	out = run(CLAUDE, "verbose")
	bash = out.index("03:19:05  Bash  ✗  0.4s")
	assert out[bash + 1 : bash + 7] == ["\tcommand", "\t\tmake", "\t\t&& echo done", "\tdescription", "\t\tBuild Dafny", "\tout"]
	edit = out.index("03:19:10  Edit  ✓  1.4s")
	assert out[edit + 1 : edit + 10] == [
		"\tfile_path", "\t\t/repo/a.py", "\told_string", "\t\tx = 1", "\t\ty = 2", "\tnew_string", "\t\tx = 3", "\tout", "\t\tedited",
	]
	assert not any("{" in row for row in out)


def test_codex_trace_is_detected_from_its_events(run):
	assert run(CODEX, "short") == [
		"02:01:05  out    Reading prompt from stdin...",
		"02:01:12  shell  ls /repo",
		"02:01:14  error  exit 2: ls /repo",
		"02:01:15  text   Done.",
	]
	verbose = run(CODEX, "verbose")
	assert verbose[1:6] == ["02:01:12  shell  ✗  2.0s", "\tcommand", "\t\tls /repo", "\tout", "\t\texit 2"]


def test_a_directory_is_rejected_with_a_one_line_error(tmp_path):
	with pytest.raises(SystemExit) as exit:
		trajectory_view.main([str(tmp_path)])
	assert exit.value.code == "trajectory_view: expected a trace file such as runner_output.log, got a directory"
