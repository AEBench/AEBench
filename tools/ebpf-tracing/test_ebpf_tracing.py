"""Tests that recorded file events are parsed and linked to the command that caused them."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import link  # noqa: E402
import record  # noqa: E402

TICK = link.NS_PER_TICK


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
	return list(link.link(raw, records))


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


def test_the_cgroup_id_is_the_inode_of_the_container_scope(tmp_path: Path) -> None:
	scope = tmp_path / "system.slice" / "docker-abc.scope"
	scope.mkdir(parents=True)

	assert record.cgroup_id("abc", root=tmp_path) == scope.stat().st_ino


def test_the_recorder_runs_privileged_next_to_the_traced_container() -> None:
	run = record.command(42)

	assert run[:2] == ["docker", "run"] and run[-2:] == [record.IMAGE, "42"]
	assert "--privileged" in run and "--pid=host" in run
	assert record.clock_header().startswith("clock\t")
