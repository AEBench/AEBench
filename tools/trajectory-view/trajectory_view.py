#!/usr/bin/env python3
"""Prints an agent trajectory (runner_output.log) in short, expanded or verbose form."""

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path

PREFIX = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+")
SHELL_WRAPPER = re.compile(r"^/bin/(?:ba)?sh -lc (['\"])(.*)\1$", re.DOTALL)
INPUT_KEYS = ("command", "file_path", "path", "pattern", "url", "description", "prompt")


def trace_format(lines):
	# The first JSON events tell Claude Code from Codex.
	for _, payload in lines[:50]:
		kind = str(payload.get("type", "")) if isinstance(payload, dict) else ""
		if kind == "thread.started" or kind.startswith("item."):
			return "codex"
		if kind in ("system", "assistant", "user", "result"):
			return "claude_code"
	return "raw"


def read_lines(path):
	secrets = [v for k, v in os.environ.items() if len(v) >= 8 and any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET"))]
	lines = []
	for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
		for value in secrets:
			raw = raw.replace(value, "[REDACTED]")
		line, ts = raw.strip(), None
		if match := PREFIX.match(line):
			ts, line = match.group(1), line[match.end():]
		try:
			payload = json.loads(line)
		except ValueError:
			payload = None
		lines.append((ts, payload if isinstance(payload, dict) else line))
	return lines


def content_text(content):
	# Tool results hold a string or a list of text (and image) blocks.
	if isinstance(content, list):
		return "\n".join(b.get("text", f"[{b.get('type')}]") if isinstance(b, dict) else str(b) for b in content)
	return "" if content is None else str(content)


def events(lines, fmt):
	"""Yields dicts with ts, kind (call/result/text/error/done/out) and their fields."""
	for ts, p in lines:
		if not isinstance(p, dict):
			if p:
				yield {"ts": ts, "kind": "out", "text": p}
			continue
		kind = p.get("type")
		if fmt == "claude_code":
			ts = p.get("timestamp") or ts
			blocks = (p.get("message") or {}).get("content") if kind in ("assistant", "user") else None
			for b in blocks if isinstance(blocks, list) else []:
				if b.get("type") == "text" and kind == "assistant":
					yield {"ts": ts, "kind": "text", "text": b.get("text")}
				elif b.get("type") == "tool_use":
					yield {"ts": ts, "kind": "call", "id": b.get("id"), "name": str(b.get("name")), "input": b.get("input") or {}}
				elif b.get("type") == "tool_result":
					yield {"ts": ts, "kind": "result", "id": b.get("tool_use_id"), "text": content_text(b.get("content")), "failed": bool(b.get("is_error"))}
			if kind == "result":
				turns = p.get("num_turns")
				reason = p.get("subtype") or p.get("stop_reason") or "finished"
				yield {"ts": ts, "kind": "done", "text": f"{reason}, {turns} turns" if isinstance(turns, int) else reason}
		elif fmt == "codex":
			item, started = p.get("item") or {}, kind == "item.started"
			if kind == "error":
				yield {"ts": ts, "kind": "error", "text": p.get("message")}
			elif kind == "turn.completed":
				yield {"ts": ts, "kind": "done", "text": "turn completed"}
			elif item.get("type") == "command_execution":
				match = SHELL_WRAPPER.match(str(item.get("command")))
				command = match.group(2) if match else str(item.get("command"))
				if started:
					yield {"ts": ts, "kind": "call", "id": item.get("id"), "name": "shell", "input": {"command": command}}
				elif kind == "item.completed":
					code = item.get("exit_code")
					yield {"ts": ts, "kind": "result", "id": item.get("id"), "text": str(item.get("aggregated_output") or ""), "failed": code not in (0, None), "exit": code, "command": command}
			elif item.get("type") == "agent_message" and kind == "item.completed":
				yield {"ts": ts, "kind": "text", "text": item.get("text")}
			elif item.get("type") == "file_change" and kind == "item.completed":
				# Codex reports a file change once done, so it is shown as one call.
				yield {"ts": ts, "kind": "call", "id": item.get("id"), "name": "edit", "input": {"changes": item.get("changes", [])}}
		elif fmt == "raw":
			yield {"ts": ts, "kind": "out", "text": json.dumps(p)}


def clock(ts):
	return ts[11:19] if ts else ""


def seconds(start, end):
	try:
		return (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
	except (AttributeError, ValueError):
		return None


def cut(text, width):
	return text if len(text) <= width else text[: max(width - 3, 0)] + "..."


def line(ts, label, summary, width):
	return cut(f"{clock(ts):<8}  {label:<6} {' '.join(str(summary or '').split())}".rstrip(), width)


def summary(event):
	"""The one-line summary trace follow prints for an event, as (label, text), or None."""
	if event["kind"] == "call":
		value = next((event["input"][k] for k in INPUT_KEYS if event["input"].get(k)), None)
		if event["name"] == "edit":
			value = ", ".join(str(c.get("path")) for c in event["input"]["changes"])
		return event["name"], value if value is not None else json.dumps(event["input"])
	if event["kind"] == "result":
		if not event["failed"]:
			return None
		return "error", f"exit {event['exit']}: {event['command']}" if "exit" in event else event["text"]
	return event["kind"], event["text"]


def block(text, depth, width, limit=None):
	"""Indents text by depth tabs, wrapped to the width, keeping only limit source lines."""
	rows = str(text).rstrip("\n").split("\n")
	shown = rows if limit is None else rows[:limit]
	room = max(width - 8 * depth, 20)
	out = ["\t" * depth + part for row in shown for part in (textwrap.wrap(row, room, break_on_hyphens=False) or [""])]
	if len(rows) > len(shown):
		out.append("\t" * depth + f"... {len(rows) - len(shown)} more lines")
	return out


def fields(value, depth, width):
	"""Field names on their own line, values one tab deeper; never raw JSON."""
	if isinstance(value, dict):
		return [r for k, v in value.items() for r in ["\t" * depth + str(k)] + fields(v, depth + 1, width)]
	if isinstance(value, list):
		if all(not isinstance(v, (dict, list)) for v in value):
			return [r for v in value for r in fields(v, depth, width)]
		return [r for i, v in enumerate(value, 1) for r in ["\t" * depth + str(i)] + fields(v, depth + 1, width)]
	return block("" if value is None else value, depth, width)


def output_text(event):
	if "exit" in event and event["failed"]:
		return f"exit {event['exit']}\n{event['text']}".rstrip("\n")
	return event["text"]


def render(evs, mode, width):
	results = {e["id"]: e for e in evs if e["kind"] == "result"}
	calls = {e["id"] for e in evs if e["kind"] == "call"}
	for e in evs:
		if mode == "short":
			if part := summary(e):
				yield line(e["ts"], *part, width)
		elif e["kind"] == "result" and mode == "verbose" and e["id"] in calls:
			continue
		elif e["kind"] == "call" and mode == "verbose":
			result = results.get(e["id"])
			mark = "" if result is None else "✗" if result["failed"] else "✓"
			took = seconds(e["ts"], result["ts"]) if result else None
			yield cut(f"{clock(e['ts']):<8}  {e['name']}  {mark}  {'' if took is None else f'{took:.1f}s'}".rstrip(), width)
			yield from fields(e["input"], 1, width)
			if result is not None:
				yield "\tout"
				yield from block(output_text(result), 2, width, 20)
		elif e["kind"] == "result" and not e["failed"]:
			yield line(e["ts"], "out", e["text"].strip().split("\n")[0], width)
		elif e["kind"] == "result":
			yield line(e["ts"], "out ✗", "", width)
			yield from block(output_text(e), 1, width, 5)
		elif e["kind"] in ("text", "error"):
			yield line(e["ts"], e["kind"], "", width)
			yield from block(e["text"] or "", 1, width, None if e["kind"] == "text" else 5)
		elif part := summary(e):
			yield line(e["ts"], *part, width)


def main(argv=None):
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("path", help="a trace file such as runner_output.log")
	group = parser.add_mutually_exclusive_group()
	for mode in ("short", "expanded", "verbose"):
		group.add_argument(f"--{mode}", action="store_const", dest="mode", const=mode)
	args = parser.parse_args(argv)
	path = Path(args.path).expanduser()
	if path.is_dir():
		sys.exit("trajectory_view: expected a trace file such as runner_output.log, got a directory")
	lines = read_lines(path)
	evs = list(events(lines, trace_format(lines)))
	width = shutil.get_terminal_size((120, 24)).columns
	try:
		for row in render(evs, args.mode or "short", width):
			print(row)
	except BrokenPipeError:
		sys.stderr.close()


if __name__ == "__main__":
	main()
