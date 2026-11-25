"""Tests that command_view prints the monitor's recorded commands in each layout."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import command_view  # noqa: E402

SNAPSHOT = "/run/aebench-agent/state/.claude/shell-snapshots/snapshot-bash-1-abc.sh"
OUT = {}


def claude(command):
	quoted = "'" + command.replace("'", "'\\''") + "'"
	return ["/bin/bash", "-c", f"source {SNAPSHOT} 2>/dev/null || true && shopt -u extglob 2>/dev/null || true && eval {quoted} < /dev/null && pwd -P >| /tmp/claude-8a99-cwd"]


def record(cid, start, argv, ms, exit_code=0, parent=None, depth=0, out=None, modified=()):
	OUT[cid] = out
	return {
		"command_id": cid, "parent_command_id": parent, "depth": depth, "argv": argv, "cwd": "/repo",
		"exit_code": exit_code, "signal": None, "complete": True,
		"stdout": {"path": f"stream/{cid}/stdout.log" if out else None, "bytes_received": len(out or "")},
		"stderr": {"path": None, "bytes_received": 0},
		"monitors": {
			"timing": {"started_at": f"2026-11-25T{start}.000000+00:00", "duration_ms": ms},
			"file_snapshot": {"created": [], "modified": list(modified), "deleted": [], "total_created": 0, "total_modified": len(modified), "total_deleted": 0},
		},
	}


# Written as each command finishes, so children come before their parents.
RECORDS = [
	record("p", "03:18:40", ["/bin/bash", "-c", "echo AEBENCH_SHIM_PROBE_OK"], 3, out="AEBENCH_SHIM_PROBE_OK\n"),
	record("e", "03:18:44", ["/bin/bash", "-c", "env"], 46, out="PATH=/usr/bin\n"),
	record("s", "03:18:44", ["/bin/bash", "-c", "-l", "SNAPSHOT_FILE=/x.sh\nsource ~/.bashrc"], 49),
	record("l1", "03:18:55", ["/bin/bash", "/usr/bin/ldd", "--version"], 3, parent="i", depth=2),
	record("l2", "03:18:56", ["/bin/bash", "/usr/bin/ldd", "--version"], 3, parent="i", depth=2),
	record("i", "03:18:55", ["bash", "dotnet-install.sh", "--channel", "6.0"], 8024, parent="c", depth=1),
	record("c", "03:18:54", claude("cd /tmp && curl -sSL https://dot.net/x.sh -o x.sh && echo 'hi there'"), 8762, out="installed\n", modified=["x.sh"]),
	record("d", "03:20:23", ["bash", "./Scripts/dafny", "/compile:0", "test.dfy"], 792, 4, parent="f", depth=1, out="\n" + "\n".join(f"z3 line {i}" for i in range(8)) + "\n"),
	record("f", "03:20:23", claude("ls Binaries | head; ./Scripts/dafny /compile:0 test.dfy"), 925, 4, out="Boogie.dll\n"),
]


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
	monkeypatch.setenv("COLUMNS", "200")

	def run(records, mode, columns=None):
		folder = tmp_path / "monitor" / "884a64a1"
		folder.mkdir(parents=True, exist_ok=True)
		for r in records:
			if path := r["stdout"]["path"]:
				(folder / path).parent.mkdir(parents=True, exist_ok=True)
				(folder / path).write_text(OUT[r["command_id"]])
		(folder / "commands.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
		if columns:
			monkeypatch.setenv("COLUMNS", str(columns))
		command_view.main([str(folder / "commands.jsonl"), f"--{mode}"])
		return capsys.readouterr().out.splitlines()

	return run


def test_short_unwraps_claude_commands_sorted_by_start_and_hides_setup(run):
	assert run(RECORDS, "short") == [
		"03:18:54  ✓  8.8s   +3   cd /tmp && curl -sSL https://dot.net/x.sh -o x.sh && echo 'hi there'",
		"03:20:23  ✗4 0.9s   +1   ls Binaries | head; ./Scripts/dafny /compile:0 test.dfy",
	]


def test_short_cuts_lines_to_the_terminal_width(run):
	assert run(RECORDS, "short", columns=40)[1] == "03:20:23  ✗4 0.9s   +1   ls Binaries ..."


def test_expanded_names_the_deepest_failed_program_and_its_last_lines(run):
	assert run(RECORDS, "expanded")[1:] == [
		"03:20:23  ✗4 0.9s   +1   ls Binaries | head; ./Scripts/dafny /compile:0 test.dfy",
		"\tfailed",
		"\t\t./Scripts/dafny /compile:0 test.dfy   exit 4   0.8s",
		"\tout",
		"\t\t... 3 earlier lines",
		*[f"\t\tz3 line {i}" for i in range(3, 8)],
	]


def test_verbose_splits_the_command_and_collapses_repeated_programs(run):
	out = run(RECORDS, "verbose")
	assert out[:16] == [
		"03:18:54  ✓  8.8s   +3",
		"\tcommand", "\t\tcd /tmp", "\t\t&& curl -sSL https://dot.net/x.sh -o x.sh", "\t\t&& echo 'hi there'",
		"\tcwd", "\t\t/repo",
		"\tfiles", "\t\tmodified  x.sh",
		"\tstarted", "\t\t✓  8.0s   dotnet-install.sh --channel 6.0", "\t\t\t✓  0.0s   /usr/bin/ldd --version ×2",
		"\tout", "\t\tinstalled",
		"03:20:23  ✗4 0.9s   +1",
		"\tcommand",
	]
	assert "\t\tnone changed" in out
	assert not any("{" in row or "AEBENCH_SHIM" in row for row in out)


def test_long_trees_are_capped_and_different_args_fold_to_the_program_name(run):
	kids = [record(f"k{i}", f"03:30:{i:02d}", ["dafny", f"/proof:{i}"], 100, parent="r", depth=1) for i in range(3)]
	kids += [record(f"v{i}", f"03:31:{i:02d}", ["./verify", str(i)], 100, parent="r", depth=1, exit_code=i % 2) for i in range(30)]
	out = run(kids + [record("r", "03:30:00", claude("./verify-all"), 9000)], "verbose")
	started = out[out.index("\tstarted") + 1 : out.index("\tstarted") + 23]
	assert started[0] == "\t\t✓  0.3s   dafny ×3"
	assert started[1:3] == ["\t\t✓  0.1s   ./verify 0", "\t\t✗1 0.1s   ./verify 1"]
	assert started[-1] == "\t\t... 11 more"


def test_codex_wrapper_is_unwrapped_and_its_exec_is_not_repeated(run):
	command = "pwd && rg --files -g 'README*'"
	wrapper = "__CODEX_SNAPSHOT_OVERRIDE_SET_0=x\nexport PATH=/codex:$PATH\n\nexec '/bin/bash' -c '" + command.replace("'", "'\"'\"'") + "'"
	records = [
		record("w", "02:01:07", ["/bin/bash", "-c", "set -e; . \"/run/aebench-agent/.codex/shell_snapshots/01a.tmp-1\""], 5),
		record("x", "02:01:12", ["/bin/bash", "-c", command], 10, parent="o", depth=1),
		record("o", "02:01:12", ["/bin/bash", "-c", wrapper], 12),
	]
	assert run(records, "short") == [f"02:01:12  ✓  0.0s        {command}"]


def test_a_directory_is_rejected_with_a_one_line_error(tmp_path):
	with pytest.raises(SystemExit) as err:
		command_view.main([str(tmp_path)])
	assert err.value.code == "command_view: expected a commands.jsonl file, got a directory"
