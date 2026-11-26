"""Tests that recorded file events are parsed and linked to the command that caused them,
and that the recorder image and command are built from the packaged assets."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cli import _build_parser, _run_options
from models import RunOptions
from runtime import file_events

TICK = file_events.NS_PER_TICK


def ns(ticks: int) -> int:
	# Any ns inside the tick, as the kernel's division drops the remainder.
	return ticks * TICK + 1234


def opened(kind: str, nspid: int, ticks: int, path: str, mono: int = 2_000_000_000) -> str:
	return f"{kind}\t{mono}\t{nspid + 9000}\t{nspid}\t{ns(ticks)}\t{path}\n"


def fork(parent: tuple[int, int], child: tuple[int, int]) -> str:
	return f"fork\t1\t{parent[0] + 9000}\t{parent[0]}\t{ns(parent[1])}\t{child[0] + 9000}\t{child[0]}\t{ns(child[1])}\n"


# The einsum_ir pattern: c1 starts a background benchmark that writes a log
# outside the workspace; later c2 copies that log into the workspace.
RECORDS = [
	{"event": "start", "command_id": "c1", "pid": 40, "start_ticks": 5000},
	{
		"event": "end",
		"command_id": "c1",
		"pid": 40,
		"start_ticks": 5000,
		"child_pid": 41,
		"child_start_ticks": 5001,
		"orphans": [{"pid": 52, "start_ticks": 5010, "comm": "bash"}],
	},
	{
		"event": "end",
		"command_id": "c2",
		"pid": 90,
		"start_ticks": 9000,
		"child_pid": 91,
		"child_start_ticks": 9001,
		"orphans": None,
	},
]
RAW = [
	"clock\t1000000000000\t1000000000\n",
	"Attaching 3 probes...\n",
	fork((41, 5001), (52, 5010)),
	fork((52, 5010), (60, 5020)),
	opened("open_write", 60, 5020, "/home/agent/benchroot/logs/tt/run.log"),
	fork((91, 9001), (95, 9100)),
	opened("open_read", 95, 9100, "/home/agent/benchroot/logs/tt/run.log"),
	opened("open_write", 95, 9100, "/repo/evaluation/logs/tt.log"),
]


def rows(raw: list[str], records: list[dict] = RECORDS) -> list[dict]:
	return list(file_events.link(raw, records))


def test_events_go_to_the_command_that_started_their_process() -> None:
	header, *events = rows(RAW)

	assert header == {"recorder": "bpftrace", "lost": 0, "events": 6}
	assert [(e["event_type"], e["path"], e["command_id"]) for e in events] == [
		("close_write", "/home/agent/benchroot/logs/tt/run.log", "c1"),
		("access", "/home/agent/benchroot/logs/tt/run.log", "c2"),
		("close_write", "/repo/evaluation/logs/tt.log", "c2"),
	]
	assert events[0]["pid"] == 60 and events[0]["host_pid"] == 9060


def test_the_nearest_command_wins_for_a_nested_shell() -> None:
	# c3 is a shell that c2's bash.real started; its own files belong to c3.
	records = [*RECORDS, {"event": "end", "command_id": "c3", "pid": 95, "start_ticks": 9100}]
	raw = [*RAW, fork((95, 9100), (97, 9101)), opened("open_write", 97, 9101, "/repo/out.csv")]

	by_path = {(e["path"], e["event_type"]): e["command_id"] for e in rows(raw, records)[1:]}

	assert by_path[("/repo/evaluation/logs/tt.log", "close_write")] == "c3"
	assert by_path[("/repo/out.csv", "close_write")] == "c3"


def test_a_reused_pid_with_another_start_time_is_another_process() -> None:
	raw = [opened("open_write", 41, 7777, "/repo/late.txt")]

	(_, event) = rows(raw)

	assert event["command_id"] is None and event["pid"] == 41


def test_a_process_no_command_owns_stays_unattributed() -> None:
	# The agent CLI itself, e.g. Claude's Write tool.
	(_, event) = rows([opened("open_write", 7, 100, "/repo/notes.md")])

	assert event["command_id"] is None


def test_duplicates_pseudo_files_and_pipes_are_dropped() -> None:
	raw = [
		*RAW,
		opened("open_write", 95, 9100, "/repo/evaluation/logs/tt.log"),
		opened("open_read", 95, 9100, "/proc/self/stat"),
		opened("open_write", 95, 9100, "pipe:[123]"),
	]

	assert len(rows(raw)) == len(rows(RAW))


def test_host_side_helpers_in_the_cgroup_are_dropped() -> None:
	# runc sets up docker exec in the container's cgroup, outside its pid namespace.
	assert rows([opened("open_write", 0, 100, "/run/containerd/x/log.json")]) == [
		{"recorder": "bpftrace", "lost": 0, "events": 1}
	]


def test_a_path_with_a_tab_and_a_quote_is_kept_whole() -> None:
	path = '/repo/a\tb "c".txt'
	(_, event) = rows([opened("open_write", 95, 9100, path)], RECORDS)

	assert event["path"] == path
	assert json.loads(json.dumps(event))["path"] == path


def test_lost_events_are_counted() -> None:
	header = rows([*RAW, "Lost 12 events\n", "Lost 3 events\n"])[0]

	assert header["lost"] == 15


def test_times_are_wall_clock_seconds() -> None:
	# Recorded 1 s (monotonic) after the clock line, whose wall clock is 1000 s.
	(_, event, *_) = rows(RAW)

	assert event["t"] == 1001.0


def test_linking_files_writes_the_trace_and_counts_events(tmp_path: Path) -> None:
	raw = tmp_path / file_events.RAW_BASENAME
	raw.write_text("".join(RAW), encoding="utf-8")
	log = tmp_path / file_events.LOG_BASENAME
	# bpftrace reports lost events on stderr, which goes to the recorder log.
	log.write_text("Attached 3 probes\nLost 4 events\n", encoding="utf-8")
	commands = tmp_path / "commands.jsonl"
	commands.write_text("".join(json.dumps(r) + "\n" for r in RECORDS), encoding="utf-8")
	out = tmp_path / file_events.EVENTS_BASENAME

	info = file_events.link_files(raw, commands, out, extra=[log])

	lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
	assert lines[0]["recorder"] == "bpftrace"
	assert info.status == "recorded"
	assert info.events == len(lines) - 1 == 3
	assert info.lost == 4
	assert info.path == str(out) and info.raw_path == str(raw)


def test_linking_without_commands_still_writes_the_events(tmp_path: Path) -> None:
	raw = tmp_path / file_events.RAW_BASENAME
	raw.write_text("".join(RAW), encoding="utf-8")
	out = tmp_path / file_events.EVENTS_BASENAME

	info = file_events.link_files(raw, tmp_path / "missing.jsonl", out)

	assert info.events == 3


def test_the_cgroup_id_is_the_inode_of_the_container_scope(tmp_path: Path) -> None:
	scope = tmp_path / "system.slice" / "docker-abc.scope"
	scope.mkdir(parents=True)

	assert file_events.cgroup_id("abc", root=tmp_path) == scope.stat().st_ino


def test_the_cgroup_id_is_found_with_the_cgroupfs_driver(tmp_path: Path) -> None:
	scope = tmp_path / "docker" / "abc"
	scope.mkdir(parents=True)

	assert file_events.cgroup_id("abc", root=tmp_path) == scope.stat().st_ino


def test_a_missing_cgroup_is_an_error(tmp_path: Path) -> None:
	with pytest.raises(FileNotFoundError):
		file_events.cgroup_id("abc", root=tmp_path)


def test_the_recorder_runs_privileged_next_to_the_traced_container() -> None:
	run = file_events.recorder_command(42, "aebench-ebpf-recorder:x")

	assert run[:2] == ["docker", "run"] and run[-2:] == ["aebench-ebpf-recorder:x", "42"]
	assert "--privileged" in run and "--pid=host" in run and "--rm" in run
	assert run[run.index("--name") + 1] == file_events.recorder_name(42)
	assert file_events.recorder_name(42).startswith("aebench-ebpf-")
	assert "/sys/kernel/tracing:/sys/kernel/tracing" in run
	assert file_events.clock_header().startswith("clock\t")


def test_the_assets_are_shipped_next_to_the_module() -> None:
	assert (file_events.ASSETS_DIR / "Dockerfile").is_file()
	assert (file_events.ASSETS_DIR / "file_events.bt").is_file()


def test_the_image_tag_changes_with_the_script(tmp_path: Path) -> None:
	assets = tmp_path / "assets"
	shutil.copytree(file_events.ASSETS_DIR, assets)
	before = file_events.image_tag(assets)
	assert before == file_events.image_tag(file_events.ASSETS_DIR)
	assert before.startswith(f"{file_events.IMAGE}:")

	script = assets / "file_events.bt"
	script.write_text(script.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8")

	assert file_events.image_tag(assets) != before


def _fake_docker(monkeypatch: pytest.MonkeyPatch, *, present: bool, built: int = 0) -> list:
	calls: list[list[str]] = []

	def run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
		calls.append(cmd)
		if cmd[:3] == ["docker", "image", "inspect"]:
			return subprocess.CompletedProcess(cmd, 0 if present else 1, "", "")
		return subprocess.CompletedProcess(cmd, built, "", "")

	monkeypatch.setattr(file_events.subprocess, "run", run)
	return calls


def test_a_missing_image_is_built_from_the_packaged_assets(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	calls = _fake_docker(monkeypatch, present=False)

	file_events.ensure_image("aebench-ebpf-recorder:t", log=tmp_path / "infra.log")

	build = calls[1]
	assert build[:2] == ["docker", "build"]
	assert build[build.index("-t") + 1] == "aebench-ebpf-recorder:t"
	assert build[-1] == str(file_events.ASSETS_DIR)
	assert "aebench-ebpf-recorder:t" in (tmp_path / "infra.log").read_text(encoding="utf-8")


def test_a_present_image_is_not_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	calls = _fake_docker(monkeypatch, present=True)

	file_events.ensure_image("aebench-ebpf-recorder:t", log=tmp_path / "infra.log")

	assert len(calls) == 1


def test_a_failed_build_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	_fake_docker(monkeypatch, present=False, built=1)

	with pytest.raises(RuntimeError, match="build"):
		file_events.ensure_image("aebench-ebpf-recorder:t", log=tmp_path / "infra.log")


def test_file_events_are_on_by_default() -> None:
	assert RunOptions().file_events is True
	args = _build_parser().parse_args(["case", "monitor", "c", "--agent", "a", "--model", "m"])

	assert _run_options(args).file_events is True


def test_no_file_events_turns_the_recorder_off() -> None:
	args = _build_parser().parse_args(
		["case", "monitor", "c", "--agent", "a", "--model", "m", "--no-file-events"]
	)

	assert _run_options(args).file_events is False
