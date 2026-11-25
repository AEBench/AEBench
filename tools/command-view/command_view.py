#!/usr/bin/env python3
"""Prints the shell commands an agent ran (monitor commands.jsonl) in short, expanded or verbose form."""

import argparse
import json
import os
import re
import shlex
import shutil
import sys
import textwrap
from pathlib import Path

LOG = "commands.jsonl"
SHELLS = ("sh", "bash", "dash", "bash.real")
# Claude Code: source <snapshot> ... && eval '<command>' < /dev/null && pwd -P >| /tmp/claude-...-cwd
CLAUDE_WRAPPER = re.compile(r"^source \S+ .*? && eval (.*?)(?: < /dev/null)? && pwd -P >\| /tmp/claude-\w+-cwd$", re.DOTALL)
# Codex: snapshot variable overrides, then exec '/bin/bash' -c '<command>'
CODEX_WRAPPER = re.compile(r"__CODEX_SNAPSHOT_.*\nexec (.*)$", re.DOTALL)
# Harness probe, Claude's env check and login-shell snapshot, Codex's snapshot write and check
SETUP = re.compile(r"^(echo AEBENCH_SHIM_PROBE_OK|env|SNAPSHOT_FILE=.*|set -e; \. \S+/shell_snapshots/\S+|.*echo '# Snapshot file'.*)$", re.DOTALL)
TREE_ROWS, FILE_ROWS = 20, 10


def shell_command(argv):
	"""The command a record ran: a shell's -c string, unwrapped, or else its argv."""
	argv = [str(a) for a in argv or []]
	if argv and Path(argv[0]).name in SHELLS:
		flag = next((i for i, a in enumerate(argv[1:], 1) if re.fullmatch(r"-\w*c\w*", a)), None)
		script = next((a for a in argv[flag + 1:] if not a.startswith("-")), None) if flag else None
		if script is not None:
			return unwrap(script)
		if len(argv) > 1 and not argv[1].startswith("-"):
			argv = argv[1:]  # a script run by its interpreter
	return shlex.join(argv)


def unwrap(text):
	for pattern in (CLAUDE_WRAPPER, CODEX_WRAPPER):
		if match := pattern.search(text):
			try:
				words = shlex.split(match.group(1))
			except ValueError:
				return text
			return shell_command(words) if pattern is CODEX_WRAPPER else " ".join(words)
	return text


def load(path):
	records = []
	for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
		try:
			record = json.loads(raw)
		except ValueError:
			continue
		if isinstance(record, dict):
			records.append(record)
	# Records are written as commands finish, so children come before parents.
	return sorted(records, key=lambda r: timing(r).get("started_at") or "")


def tree(records):
	"""The visible top-level commands as nodes holding their record, command and child nodes."""
	ids = {r.get("command_id") for r in records}
	kids, roots = {}, []
	for r in records:
		parent = r.get("parent_command_id")
		(kids.setdefault(parent, []) if parent is not None and parent in ids else roots).append(r)

	def node(r):
		cmd, below = shell_command(r.get("argv")), [node(k) for k in kids.get(r.get("command_id"), [])]
		# A wrapper that execs its own command (Codex) records it twice; keep one.
		below = [g for k in below for g in (k["kids"] if k["cmd"] == cmd else [k])]
		return {"r": r, "cmd": cmd, "kids": below}

	return [n for n in map(node, roots) if not SETUP.match(n["cmd"])]


def walk(n, depth=0):
	yield depth, n
	for k in n["kids"]:
		yield from walk(k, depth + 1)


def timing(r):
	return (r.get("monitors") or {}).get("timing") or {}


def mark(r):
	sig, code = r.get("signal"), r.get("exit_code")
	if sig is not None:
		return f"✗{sig}" if isinstance(sig, str) else f"✗sig{sig}"
	return "?" if code is None else "✓" if code == 0 else f"✗{code}"


def took(ms):
	if not isinstance(ms, (int, float)):
		return ""
	s = ms / 1000
	if s < 60:
		return f"{s:.1f}s"
	return f"{int(s // 60)}m{int(s % 60):02d}s" if s < 3600 else f"{int(s // 3600)}h{int(s % 3600 // 60):02d}m"


def cut(text, width):
	return text if len(text) <= width else text[: max(width - 3, 0)] + "..."


def flat(text):
	return " ".join(str(text).split())


def block(text, depth, width, limit=None):
	"""Indents text by depth tabs, wrapped to the width, keeping only limit source lines."""
	rows = str(text).rstrip("\n").split("\n")
	shown = rows if limit is None else rows[:limit]
	room = max(width - 8 * depth, 20)
	out = ["\t" * depth + part for row in shown for part in (textwrap.wrap(row, room, break_on_hyphens=False) or [""])]
	if len(rows) > len(shown):
		out.append("\t" * depth + f"... {len(rows) - len(shown)} more lines")
	return out


def read(r, stream, base):
	path = (r.get(stream) or {}).get("path")
	try:
		text = (base / path).read_text(encoding="utf-8", errors="replace") if path else ""
	except OSError:
		text = ""
	for value in (v for k, v in os.environ.items() if len(v) >= 8 and any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET"))):
		text = text.replace(value, "[REDACTED]")
	return text.strip("\n")


def line(n, width, command=True):
	r, count = n["r"], sum(1 for _ in walk(n)) - 1
	head = f"{timing(r).get('started_at', '')[11:19]:<8}  {mark(r):<3}{took(timing(r).get('duration_ms')):<7}{f'+{count}' if count else '':<5}"
	return cut((head + flat(n["cmd"]) if command else head).rstrip(), width)


def split(cmd):
	"""Splits a command at top-level &&, ; and |, or keeps it whole when that is not simple."""
	if "\n" in cmd or "<<" in cmd:
		return [cmd]
	parts, start, depth, quote, i = [], 0, 0, None, 0
	while i < len(cmd):
		c = cmd[i]
		if c == "\\" and quote != "'":
			i += 2
			continue
		if quote:
			quote = None if c == quote else quote
		elif c in "'\"`":
			quote = c
		elif c in "({":
			depth += 1
		elif c in ")}":
			depth -= 1
		elif depth == 0 and c == ";":
			parts, start = parts + [cmd[start : i + 1]], i + 1
		elif depth == 0 and (cmd.startswith("&&", i) or (c == "|" and cmd[i - 1 : i] not in "|>" and cmd[i + 1 : i + 2] not in ("|", "&"))):
			parts, start = parts + [cmd[start:i]], i
			i += 1 if c == "&" else 0
		i += 1
	if quote or depth:
		return [cmd]
	return [p.strip() for p in parts + [cmd[start:]] if p.strip()]


def files(r):
	snap = (r.get("monitors") or {}).get("file_snapshot") or {}
	rows = []
	for kind in ("created", "modified", "deleted"):
		paths = snap.get(kind) or []
		rows += [f"\t\t{kind}  {p}" for p in paths[:FILE_ROWS]]
		if (more := max(snap.get(f"total_{kind}") or 0, len(paths)) - len(paths[:FILE_ROWS])) > 0:
			rows.append(f"\t\t... {more} more {kind}")
	return rows or ["\t\tnone changed"]


def program(cmd):
	return Path(cmd.split(maxsplit=1)[0]).name if cmd.strip() else ""


def started(kids, depth):
	"""Yields (depth, text, programs) per child, folding runs of the same leaf program into one ×N row."""
	i = 0
	while i < len(kids):
		k, run = kids[i], [kids[i]]
		while not k["kids"] and i + len(run) < len(kids):
			nxt = kids[i + len(run)]
			if nxt["kids"] or program(nxt["cmd"]) != program(k["cmd"]) or mark(nxt["r"]) != mark(k["r"]):
				break
			run.append(nxt)
		i += len(run)
		text = k["cmd"] if all(x["cmd"] == k["cmd"] for x in run) else program(k["cmd"])
		ms = sum(timing(x["r"]).get("duration_ms") or 0 for x in run)
		yield depth, f"{mark(k['r']):<3}{took(ms):<7}{flat(text)}" + (f" ×{len(run)}" if len(run) > 1 else ""), len(run)
		yield from started(k["kids"], depth + 1)


def failure(n, base, width):
	"""The deepest failed program of a command, with the end of its output."""
	_, worst = max(((d, k) for d, k in walk(n) if mark(k["r"]).startswith("✗")), key=lambda p: p[0])
	r, sig = worst["r"], worst["r"].get("signal")
	tail = f"   {'exit ' + str(r.get('exit_code')) if sig is None else 'signal ' + str(sig)}   {took(timing(r).get('duration_ms'))}"
	yield "\tfailed"
	yield "\t\t" + cut(flat(worst["cmd"]), width - 16 - len(tail)) + tail
	if out := read(r, "stdout", base) or read(r, "stderr", base):
		rows = out.split("\n")
		yield "\tout"
		if len(rows) > 5:
			yield f"\t\t... {len(rows) - 5} earlier lines"
		yield from block("\n".join(rows[-5:]), 2, width)


def render(nodes, mode, base, width):
	for n in nodes:
		r = n["r"]
		if mode != "verbose":
			yield line(n, width)
			if mode == "expanded" and mark(r).startswith("✗"):
				yield from failure(n, base, width)
			continue
		yield line(n, width, command=False)
		yield "\tcommand"
		for part in split(n["cmd"]):
			yield from block(part, 2, width)
		yield "\tcwd"
		yield from block(r.get("cwd") or "", 2, width)
		yield "\tfiles"
		yield from files(r)
		if rows := list(started(n["kids"], 2)):
			yield "\tstarted"
			yield from ("\t" * d + cut(text, width - 8 * d) for d, text, _ in rows[:TREE_ROWS])
			if rest := rows[TREE_ROWS:]:
				yield f"\t\t... {sum(count for _, _, count in rest)} more"
		for label, stream in (("out", "stdout"), ("err", "stderr")):
			if text := read(r, stream, base):
				yield f"\t{label}"
				yield from block(text, 2, width, 20)


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("path", help="a monitor commands.jsonl file")
	group = parser.add_mutually_exclusive_group()
	for mode in ("short", "expanded", "verbose"):
		group.add_argument(f"--{mode}", action="store_const", dest="mode", const=mode)
	args = parser.parse_args(argv)
	log = Path(args.path).expanduser()
	if log.is_dir():
		sys.exit(f"command_view: expected a {LOG} file, got a directory")
	if not log.is_file():
		sys.exit(f"command_view: no such file: {log}")
	width = shutil.get_terminal_size((120, 24)).columns
	try:
		for row in render(tree(load(log)), args.mode or "short", log.parent, width):
			print(row)
	except BrokenPipeError:
		sys.stderr.close()


if __name__ == "__main__":
	main()
