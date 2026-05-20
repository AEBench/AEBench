from __future__ import annotations

import csv
import json
from pathlib import Path


PROTOCOLS: tuple[str, ...] = (
	"clientServer",
	"ringLeaderElection",
	"simplifiedLeaderElection",
	"twoPhaseCommit",
	"paxos",
	"flexPaxos",
	"distributedLock",
	"shardedKv",
	"shardedKvBatched",
	"lockServer",
)


def load_json_file(path: Path, *, label: str) -> object:
	"""Read and parse a JSON file, raising ValueError on failure."""
	try:
		text = path.read_text(encoding="utf-8")
	except OSError as exc:
		raise ValueError(f"{label}: failed to read {path}: {exc}") from exc

	text = text.strip()
	if not text:
		raise ValueError(f"{label}: empty JSON content at {path}")

	try:
		return json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError(f"{label}: invalid JSON in {path}: {exc}") from exc


def load_sloc_csv(path: Path) -> dict[str, dict[str, int]]:
	"""Parse sloc.csv into {protocol: {column: value}}."""
	try:
		text = path.read_text(encoding="utf-8")
	except OSError as exc:
		raise ValueError(f"failed to read sloc.csv: {exc}") from exc

	rows: dict[str, dict[str, int]] = {}
	reader = csv.DictReader(text.strip().splitlines())
	for row in reader:
		protocol = row.get("protocol", "").strip()
		if not protocol:
			continue
		rows[protocol] = {
			"sync_spec": int(row.get("sync_spec", 0)),
			"manual_proof": int(row.get("manual_proof", 0)),
			"sync_proof": int(row.get("sync_proof", 0)),
		}
	return rows
