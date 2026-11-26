#!/usr/bin/env python3
"""Links recorded file events to the commands of a monitored run.

Usage: link.py <raw.tsv> <commands.jsonl> <file_events.jsonl>

A process is (container pid, start in clock ticks). Each event goes to the
nearest command that owns its process or an ancestor at fork time: the
shim, its bash.real child, or an orphan it adopted.
"""

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

# The kernel's /proc/<pid>/stat start time is start_boottime in USER_HZ ticks.
NS_PER_TICK = 10_000_000
# Raw kinds, and the event_type each is written as; the reader of file_events.jsonl
# counts close_write (and moves) as writes and every other type as a read.
EVENT_TYPES = {"open_write": "close_write", "open_read": "access", "open_exec": "open_exec"}
FILE_KINDS = tuple(EVENT_TYPES)
SKIPPED_PREFIXES = ("/proc/", "/sys/", "/dev/")

Process = tuple[int, int]


def process(nspid: str, start_ns: str) -> Process:
	return int(nspid), int(start_ns) // NS_PER_TICK


def owners(records: Iterable[dict]) -> dict[Process, str]:
	"""Each process a command record names, mapped to its command_id."""
	found: dict[Process, str] = {}
	for record in records:
		if record.get("event") == "start" or "command_id" not in record:
			continue
		cid = record["command_id"]
		pairs = [(record.get("pid"), record.get("start_ticks"))]
		pairs.append((record.get("child_pid"), record.get("child_start_ticks")))
		pairs += [(o.get("pid"), o.get("start_ticks")) for o in record.get("orphans") or ()]
		for pid, ticks in pairs:
			if pid is not None and ticks is not None:
				# A shim's own processes win over another command's orphan list.
				found.setdefault((int(pid), int(ticks)), cid)
	return found


def parse(lines: Iterable[str]) -> tuple[tuple[int, int] | None, list[list[str]], int]:
	"""(wall and monotonic ns at start, event fields, lost events) of a raw file."""
	clock, events, lost = None, [], 0
	for line in lines:
		line = line.rstrip("\n")
		kind = line.split("\t", 1)[0]
		if kind in FILE_KINDS:
			# The path is last and may hold a tab, so split off five fields only.
			fields = line.split("\t", 5)
			if len(fields) == 6:
				events.append(fields)
		elif kind == "fork":
			fields = line.split("\t")
			if len(fields) == 8:
				events.append(fields)
		elif kind == "clock":
			fields = line.split("\t")
			clock = int(fields[1]), int(fields[2])
		elif line.startswith("Lost "):
			# bpftrace's own report of events the kernel buffer dropped.
			lost += int(line.split()[1])
	return clock, events, lost


def link(raw: Iterable[str], records: Iterable[dict]) -> Iterator[dict]:
	"""A header line, then one row per (command, path, event type)."""
	clock, events, lost = parse(raw)
	owner = owners(records)
	parent: dict[Process, Process] = {}
	for fields in events:
		if fields[0] == "fork":
			parent[process(fields[6], fields[7])] = process(fields[3], fields[4])

	def command_of(proc: Process) -> str | None:
		seen = set()
		while proc not in seen:
			if proc in owner:
				return owner[proc]
			seen.add(proc)
			if proc not in parent:
				return None
			proc = parent[proc]
		return None

	yield {"recorder": "bpftrace", "lost": lost, "events": len(events)}
	kept = set()
	for kind, mono, host_pid, nspid, start, path in (f for f in events if f[0] != "fork"):
		# Container pid 0: a host-side helper in the cgroup, such as runc for docker exec.
		if nspid == "0" or not path.startswith("/") or path.startswith(SKIPPED_PREFIXES):
			continue
		proc = process(nspid, start)
		cid = command_of(proc)
		key = (cid or proc, path, kind)
		if key in kept:
			continue
		kept.add(key)
		t = None if clock is None else (clock[0] + int(mono) - clock[1]) / 1e9
		yield {
			"t": t,
			"event_type": EVENT_TYPES[kind],
			"path": path,
			"command_id": cid,
			"pid": proc[0],
			"host_pid": int(host_pid),
		}


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("raw", type=Path)
	parser.add_argument("commands", type=Path)
	parser.add_argument("out", type=Path)
	args = parser.parse_args()
	with args.commands.open(encoding="utf-8") as handle:
		records = [json.loads(line) for line in handle if line.strip()]
	with (
		args.raw.open(encoding="utf-8", errors="replace") as raw,
		args.out.open("w", encoding="utf-8") as out,
	):
		for row in link(raw, records):
			out.write(json.dumps(row) + "\n")
	return 0


if __name__ == "__main__":
	sys.exit(main())
