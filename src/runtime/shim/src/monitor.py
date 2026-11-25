import argparse
import asyncio
import hashlib
import json
import os
import signal as signallib
import socket as socketlib
import stat as statlib
import struct
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, TypeAlias

Frame: TypeAlias = tuple[int, bytes]
Record: TypeAlias = dict[str, object]
FileStat: TypeAlias = tuple[int, int, int, int]  # (size, mtime_ns, ctime_ns, inode)

SOCKET_PATH = "/run/aebench/command.sock"
LOG_BASENAME = "commands.jsonl"

STREAMS_DIRNAME = "stream"

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024

MAX_LISTED_PATHS = 100
MAX_SNAPSHOT_FILES = 20000  # Limit for full-workspace snapshots only
TRACKED_FILES_BASENAME = "tracked_files.json"  # Host-provided list of files to track
SNAPSHOT_BUDGET_SECONDS = 2.0  # Timeout for snapshots; does not fail the command
MAX_HASH_BYTES = 64 * 1024 * 1024  # Larger changed files get a null hash
HASH_BUDGET_SECONDS = 2.0
_HASH_CHUNK_BYTES = 1024 * 1024
SUBREAPER = True  # Shims adopt and report the processes their command leaves running

COMMAND_INFO = 0
STDOUT = 1
STDERR = 2
END = 3
DECISION = 4
STARTED = 5
# A client asks for a snapshot of the workspace subvolume and waits until it is taken.
WORKDIR_SNAPSHOT = 6
WORKDIR_SNAPSHOT_DONE = 7

WORKDIR_SNAPSHOTS_BASENAME = "workdir_snapshots.jsonl"
# Exit status when the final snapshot, taken on SIGTERM, failed.
EXIT_FINAL_SNAPSHOT_FAILED = 3


async def read_buffer(reader: asyncio.StreamReader, size: int) -> bytes | None:
	"""Reads exactly <size> bytes from a shell shim stream socket."""
	try:
		return await reader.readexactly(size)
	except asyncio.IncompleteReadError as exc:
		if not exc.partial:
			return None

		raise EOFError("connection ended mid-frame") from exc


async def read_frame(reader: asyncio.StreamReader) -> Frame | None:
	"""Read one complete message from a shell shim socket."""
	header = await read_buffer(reader, 5)

	if header is None:
		return None

	length = struct.unpack(">I", header[:4])[0]
	message_type = header[4]

	if length > MAX_FRAME_BYTES:
		raise ValueError("frame too large")

	payload = await read_buffer(reader, length)

	if payload is None and length:
		raise EOFError("connection ended mid-frame")

	return message_type, payload or b""


async def send_frame(
	writer: asyncio.StreamWriter,
	message_type: int,
	payload: bytes,
) -> None:
	header = struct.pack(">I", len(payload)) + bytes([message_type])
	writer.write(header + payload)
	await writer.drain()


class StreamSink:
	"""One captured stream of a command, written as frames arrive"""

	def __init__(self, path: str) -> None:
		self.path = path
		self.ceiling = MAX_STREAM_BYTES

		# Keep track of how much will be truncated
		self.received = 0
		self.kept = 0
		self.tty = False

		self.handle: BinaryIO | None = None

	def write(self, chunk: bytes) -> None:
		"""Appends the bytes in `chunk` to this stream's file.
		Bytes past the ceiling are counted and then dropped.
		"""
		self.received += len(chunk)

		room = self.ceiling - self.kept
		# Truncate if exceed ceil
		if room <= 0:
			return

		keep = chunk[:room]

		if self.handle is None:
			# Opened on the first byte
			os.makedirs(os.path.dirname(self.path), exist_ok=True)
			self.handle = open(self.path, "wb")

		self.handle.write(keep)
		self.kept += len(keep)

	def close(self) -> None:
		"""Run it before summary; closes stream file"""
		if self.handle is not None:
			self.handle.close()
			self.handle = None

	def summary(self, output_dir: str) -> Record:
		if self.kept < self.received:
			capture = "truncated"
		elif self.kept:
			capture = "captured"
		elif self.tty:
			capture = "skipped_tty"
		else:
			capture = "none"

		return {
			"capture": capture,
			"bytes_received": self.received,
			"bytes_kept": self.kept,
			"path": os.path.relpath(self.path, output_dir) if self.kept else None,
		}


class CommandContext:
	"""What a monitor is given about one command."""

	def __init__(self, record: Record, output_dir: str, run_id: str) -> None:
		self.record = record
		self.output_dir = output_dir
		self.run_id = run_id
		# Each monitor stores a different shape (CommandTiming or FileSnapshot) here and only ever reads back its own entry.
		self.state: dict[str, Any] = {}


class Monitor:
	"""Base class for per-command monitoring. Override is possible"""

	name = "monitor"

	def before_command(self, ctx: CommandContext) -> None:
		"""Runs before the command starts, so pre-command state can be read."""

	def after_command(self, ctx: CommandContext) -> Record | None:
		"""Runs once the command is over. Whatever it returns lands on the
		record under this monitor's name."""
		return None


MONITORS: list[Monitor] = []


def register(monitor: Monitor) -> None:
	MONITORS.append(monitor)


def note_monitor_error(record: Record, name: str, phase: str, exc: BaseException) -> None:
	"""A broken monitor is recorded, never raised. The command's record is still written in full.
	A broken monitor can never cost a record.
	"""
	errors = record.setdefault("monitor_errors", [])
	assert isinstance(errors, list)

	errors.append({"monitor": name, "phase": phase, "error": f"{type(exc).__name__}: {exc}"})


def run_before_command(ctx: CommandContext) -> None:
	"""Runs every monitor's pre-command phase, in registration order."""
	for monitor in MONITORS:
		try:
			monitor.before_command(ctx)
		except BaseException as exc:
			note_monitor_error(ctx.record, monitor.name, "before_command", exc)


def run_after_command(ctx: CommandContext) -> None:
	"""Runs every monitor's post-command phase, in reverse registration order."""
	for monitor in reversed(MONITORS):
		try:
			output = monitor.after_command(ctx)
		except BaseException as exc:
			note_monitor_error(ctx.record, monitor.name, "after_command", exc)
			continue

		if output is not None:
			observations = ctx.record.setdefault("monitors", {})
			assert isinstance(observations, dict)
			observations[monitor.name] = output


class CommandTiming(Monitor):
	"""How long each command took from release to end, on a monotonic clock.

	Registered first, so it brackets the snapshots. The broker stamps the
	release and the end into ctx.state.
	"""

	name = "timing"

	def before_command(self, ctx: CommandContext) -> None:
		ctx.state[self.name] = time.monotonic()

	def after_command(self, ctx: CommandContext) -> Record | None:
		before = ctx.state.get(self.name)
		released = ctx.state.get("released")

		if before is None or released is None:
			return None

		ended = ctx.state.get("ended")
		released_at = released[1].isoformat()

		return {
			"started_at": released_at,
			"released_at": released_at,
			"ended_at": ended and ended[1].isoformat(),
			"duration_ms": ended and int((ended[0] - released[0]) * 1000),
			"snapshot_before_ms": int((released[0] - before) * 1000),
			"snapshot_after_ms": ended and int((time.monotonic() - ended[0]) * 1000),
			# Wall clock covers everything the command spawned
			"includes_children": True,
		}


class FileSnapshot(Monitor):
	"""Track changes made by a command to specified files or the entire workspace."""

	name = "file_snapshot"

	def __init__(self, workspace: str | Path, tracked_files: str | Path | None = None) -> None:
		self.workspace = Path(workspace)
		self.tracked: list[str] | None = None

		if tracked_files is not None and os.path.exists(tracked_files):
			with open(tracked_files, encoding="utf-8") as handle:
				self.tracked = list(json.load(handle)["files"])
		self.tracked_set = frozenset(self.tracked or ())

	def _snapshot(self) -> tuple[dict[str, FileStat] | None, str | None]:
		if self.tracked is not None:
			return self._snapshot_tracked()
		return self._snapshot_walk()

	def _snapshot_tracked(self) -> tuple[dict[str, FileStat] | None, str | None]:
		"""Maps each tracked file still present, then every untracked one, to its FileStat."""
		assert self.tracked is not None
		found: dict[str, FileStat] = {}
		deadline = time.monotonic() + SNAPSHOT_BUDGET_SECONDS

		for rel in self.tracked:
			if time.monotonic() >= deadline:
				return None, f"snapshot exceeded {SNAPSHOT_BUDGET_SECONDS:g}s"

			try:
				# Using lstat to tracked symlink is the link itself.
				stat = os.lstat(self.workspace / rel)
			except OSError:
				# The tracked file not found, so reported as deleted.
				continue

			found[rel] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)

		return self._walk_untracked(found, deadline)

	def _walk_untracked(
		self, found: dict[str, FileStat], deadline: float
	) -> tuple[dict[str, FileStat] | None, str | None]:
		"""Adds the files outside the tracked list to `found`, skipping .git."""
		untracked = 0
		for directory, subdirectories, filenames in os.walk(self.workspace):
			kept = []
			for name in subdirectories:
				if name == ".git":
					continue
				# A symlink to a directory is an entry, not a tree to enter.
				if os.path.islink(os.path.join(directory, name)):
					filenames.append(name)
				else:
					kept.append(name)
			subdirectories[:] = kept

			for filename in filenames:
				if time.monotonic() >= deadline:
					return None, f"snapshot exceeded {SNAPSHOT_BUDGET_SECONDS:g}s"

				path = os.path.join(directory, filename)
				rel = os.path.relpath(path, self.workspace)
				if rel in self.tracked_set:
					continue
				try:
					stat = os.lstat(path)
				except OSError:
					continue

				found[rel] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
				untracked += 1
				if untracked > MAX_SNAPSHOT_FILES:
					return None, f"workspace exceeds {MAX_SNAPSHOT_FILES} untracked files"

		return found, None

	def _hashes(self, names: list[str]) -> tuple[dict[str, str | None], dict[str, str]]:
		"""SHA-256 of each path, a symlink by its target text; (hashes, skip reasons)."""
		hashes: dict[str, str | None] = {}
		skipped: dict[str, str] = {}
		deadline = time.monotonic() + HASH_BUDGET_SECONDS

		for rel in names:
			hashes[rel] = None
			if time.monotonic() >= deadline:
				skipped[rel] = "budget"
				continue
			path = os.path.join(self.workspace, rel)
			try:
				info = os.lstat(path)
				if statlib.S_ISLNK(info.st_mode):
					hashes[rel] = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
				elif not statlib.S_ISREG(info.st_mode):
					skipped[rel] = "not_regular"
				elif info.st_size > MAX_HASH_BYTES:
					skipped[rel] = "over_cap"
				else:
					hashes[rel], reason = self._hash_file(path, deadline)
					if reason is not None:
						skipped[rel] = reason
			except OSError:
				# Gone, or swapped for a symlink after the lstat.
				skipped[rel] = "unreadable"

		return hashes, skipped

	@staticmethod
	def _hash_file(path: str, deadline: float) -> tuple[str | None, str | None]:
		fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
		with os.fdopen(fd, "rb") as handle:
			if not statlib.S_ISREG(os.fstat(handle.fileno()).st_mode):
				return None, "not_regular"
			digest = hashlib.sha256()
			read = 0
			while chunk := handle.read(_HASH_CHUNK_BYTES):
				read += len(chunk)
				if read > MAX_HASH_BYTES:
					return None, "over_cap"
				if time.monotonic() >= deadline:
					return None, "budget"
				digest.update(chunk)
		return digest.hexdigest(), None

	def _snapshot_walk(self) -> tuple[dict[str, FileStat] | None, str | None]:
		"""Maps each file to its FileStat, or (None, reason) on error/exception."""
		found: dict[str, FileStat] = {}
		deadline = time.monotonic() + SNAPSHOT_BUDGET_SECONDS

		for directory, _subdirectories, filenames in os.walk(self.workspace):
			for filename in filenames:
				if time.monotonic() >= deadline:
					return None, f"snapshot exceeded {SNAPSHOT_BUDGET_SECONDS:g}s"

				path = os.path.join(directory, filename)

				try:
					stat = os.stat(path)
				except OSError:
					# Deleted mid traversal, or an error raised.
					continue

				found[os.path.relpath(path, self.workspace)] = (
					stat.st_size,
					stat.st_mtime_ns,
					stat.st_ctime_ns,
					stat.st_ino,
				)

				if len(found) > MAX_SNAPSHOT_FILES:
					return None, f"workspace exceeds {MAX_SNAPSHOT_FILES} files"

		return found, None

	def before_command(self, ctx: CommandContext) -> None:
		ctx.state[self.name] = self._snapshot()

	def after_command(self, ctx: CommandContext) -> Record | None:
		if self.name not in ctx.state:
			return None

		before, before_reason = ctx.state[self.name]
		after, after_reason = self._snapshot()

		if before is None or after is None:
			# Explains reason for skipped
			return {"skipped": before_reason or after_reason}

		created = sorted(set(after) - set(before))
		deleted = sorted(set(before) - set(after))
		modified = sorted(name for name in set(before) & set(after) if before[name] != after[name])

		if self.tracked is None:
			result: Record = {"scope": "workspace"}
			lists = {"created": created, "modified": modified, "deleted": deleted}
		else:
			# Untracked paths go only in the *_untracked lists, so created,
			# modified and deleted keep meaning tracked files, as the checker reads them.
			result = {"scope": "tracked+untracked"}
			lists = {}
			for op, names in (("created", created), ("modified", modified), ("deleted", deleted)):
				lists[op] = [name for name in names if name in self.tracked_set]
				lists[f"{op}_untracked"] = [name for name in names if name not in self.tracked_set]

		listed = {op: names[:MAX_LISTED_PATHS] for op, names in lists.items()}
		changed = [
			name
			for op in ("created", "modified", "created_untracked", "modified_untracked")
			for name in listed.get(op, ())
		]
		hashes, hash_skipped = self._hashes(changed)

		result["watched"] = len(after)
		for op, names in lists.items():
			result[f"total_{op}"] = len(names)
		result.update(listed)
		result["truncated"] = any(len(names) > MAX_LISTED_PATHS for names in lists.values())
		result["sizes"] = {name: after[name][0] for name in changed}
		result["hashes"] = hashes
		result["hash_skipped"] = hash_skipped
		return result


class ProcessTree:
	"""Links each command to the nearest recorded command above it.

	Processes are keyed by (pid, start_ticks), so a reused pid never matches.
	Entries outlive their connection, so a shell can link to a finished parent.
	"""

	def __init__(self) -> None:
		self.owners: dict[tuple[object, object], tuple[str, str]] = {}
		self.lineage: dict[str, tuple[str, int]] = {}
		self.open: set[str] = set()

	def add(
		self, pid: object, start_ticks: object, command_id: str, kind: str
	) -> tuple[str, str] | None:
		"""Records that a process belongs to a command and returns its owner."""
		if pid is None or start_ticks is None:
			return None
		# An orphan that is itself a shim keeps its own command.
		if kind == "orphan":
			return self.owners.setdefault((pid, start_ticks), (command_id, kind))
		self.owners[(pid, start_ticks)] = (command_id, kind)
		return command_id, kind

	def link(self, record: Record, ancestry: object) -> None:
		"""Sets the parent fields of a new command from its reported ancestry."""
		command_id = str(record["command_id"])
		self.open.add(command_id)
		if not isinstance(ancestry, list):
			# An older shim: the links are unknown, not absent.
			record.update(
				parent_command_id=None, root_command_id=None, depth=None, parent_link=None
			)
			return

		parent, link = None, "none"
		for ancestor in ancestry:
			owner = self.owners.get((ancestor.get("pid"), ancestor.get("start_ticks")))
			if owner is not None:
				parent, kind = owner
				if kind == "orphan":
					link = "adopted"
				else:
					link = "live" if parent in self.open else "closed"
				break

		root, depth = (command_id, 0)
		if parent is not None:
			parent_root, parent_depth = self.lineage[parent]
			root, depth = parent_root, parent_depth + 1

		self.lineage[command_id] = (root, depth)
		self.add(record.get("pid"), record.get("start_ticks"), command_id, "shim")
		record.update(parent_command_id=parent, root_command_id=root, depth=depth, parent_link=link)

	def close(self, command_id: str) -> None:
		self.open.discard(command_id)


PROCESS_TREE = ProcessTree()


class WorkdirSnapshotter:
	"""Takes btrfs snapshots of the workspace subvolume, one at a time, in request order.

	A single-worker executor runs the requests in submission order, so snapshots
	are serialized and numbered in the order they are taken, while the event loop
	keeps serving the shim's connections. Each snapshot is appended to the index
	before its requester is answered.
	"""

	def __init__(self, subvolume: str, snapshot_dir: str, index_path: str) -> None:
		# Imported here: only a snapshotting broker needs it, and it lives outside
		# this script's directory (src/runtime/btrfs.py, stdlib only).
		sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
		from runtime import btrfs

		self._btrfs = btrfs
		self.subvolume = subvolume
		self.snapshot_dir = snapshot_dir
		self.index_path = index_path
		self._next_seq = 0
		self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workdir-snapshots")

	def start(self) -> None:
		os.makedirs(self.snapshot_dir, exist_ok=True)

	def stop(self) -> None:
		"""Finishes the requests already submitted, then ends the worker."""
		self._executor.shutdown(wait=True)

	async def request(self, fields: Record) -> Record:
		"""Submits one snapshot and waits until it is taken, or has failed."""
		requested = dict(fields)
		requested["requested_at"] = datetime.now(timezone.utc).isoformat()
		return await asyncio.wrap_future(self._executor.submit(self._take_and_index, requested))

	def _take_and_index(self, fields: Record) -> Record:
		entry = self._take(fields)
		try:
			write_record(entry, self.index_path)
		except OSError as exc:
			entry["ok"] = False
			entry["error"] = f"could not write the snapshot index: {exc}"
		return entry

	def _take(self, fields: Record) -> Record:
		seq = self._next_seq
		self._next_seq += 1
		name = f"{seq:06d}"
		started = time.monotonic()
		entry: Record = {"seq": seq, **fields, "snapshot": name}
		try:
			self._btrfs.snapshot(self.subvolume, os.path.join(self.snapshot_dir, name))
			entry["ok"] = True
			entry["error"] = None
		except Exception as exc:
			entry["ok"] = False
			entry["error"] = f"{type(exc).__name__}: {exc}"
		entry["taken_at"] = datetime.now(timezone.utc).isoformat()
		entry["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
		return entry


async def process_snapshot_request(
	payload: bytes,
	writer: asyncio.StreamWriter,
	snapshotter: WorkdirSnapshotter | None,
) -> None:
	"""Takes the snapshot a client asked for, then answers with its outcome."""
	try:
		if snapshotter is None:
			reply: Record = {"ok": False, "error": "workdir snapshots are not enabled"}
		else:
			# The client decides what metadata to send (the hook sends no tool input);
			# it is recorded as sent.
			fields = json.loads(payload)
			if not isinstance(fields, dict):
				raise ValueError("snapshot request must be a JSON object")
			entry = await snapshotter.request(fields)
			reply = {key: entry.get(key) for key in ("ok", "seq", "snapshot", "error")}
	except Exception as exc:
		reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

	try:
		await send_frame(writer, WORKDIR_SNAPSHOT_DONE, json.dumps(reply).encode("utf-8"))
	except (ConnectionError, OSError) as exc:
		# The requester gave up (its hook timed out); the snapshot is still in the index.
		print(f"could not answer snapshot {reply.get('seq')}: {exc}", flush=True)
	finally:
		writer.close()
		try:
			await writer.wait_closed()
		except OSError:
			pass


def write_record(record: Record, log_path: str) -> None:
	with open(log_path, "a", encoding="utf-8") as file:
		file.write(json.dumps(record) + "\n")


async def handle_client(
	reader: asyncio.StreamReader,
	writer: asyncio.StreamWriter,
	output_dir: str,
	run_id: str,
	snapshotter: WorkdirSnapshotter | None = None,
) -> None:
	"""Dispatches one connection on its first frame: a snapshot request, or a shell."""
	first: Frame | None = None
	first_error: Exception | None = None
	try:
		first = await read_frame(reader)
	except Exception as exc:
		# Recorded by process_connection, like any other broken shell session.
		first_error = exc
	if first is not None and first[0] == WORKDIR_SNAPSHOT:
		await process_snapshot_request(first[1], writer, snapshotter)
		return
	await process_connection(
		reader, writer, output_dir, run_id, first=first, first_error=first_error, first_read=True
	)


async def process_connection(
	reader: asyncio.StreamReader,
	writer: asyncio.StreamWriter,
	output_dir: str,
	run_id: str,
	*,
	first: Frame | None = None,
	first_error: Exception | None = None,
	first_read: bool = False,
) -> None:
	"""Process and monitor the session for one shell invocation."""
	command_id = uuid.uuid4().hex

	log_path = os.path.join(output_dir, LOG_BASENAME)

	# One folder per command, so its two streams stay together.
	stream_dir = os.path.join(output_dir, STREAMS_DIRNAME, command_id)
	stdout = StreamSink(os.path.join(stream_dir, "stdout.log"))
	stderr = StreamSink(os.path.join(stream_dir, "stderr.log"))

	shim_clock: dict[str, object] = {"info": None, "spawn": None, "exit": None}
	record: Record = {
		"run_id": run_id,
		"command_id": command_id,
		"complete": False,
		"exit_code": None,
		"signal": None,
		"return_code": None,
		"end_reason": None,
		"child_pid": None,
		"child_start_ticks": None,
		"orphans": None,
		"shim_clock": shim_clock,
	}

	context = CommandContext(record, output_dir, run_id)

	try:
		if first_error is not None:
			raise first_error
		if not first_read:
			first = await read_frame(reader)

		if first is None:
			return

		message_type, payload = first

		if message_type != COMMAND_INFO:
			raise ValueError("expected command_info")

		command_info = json.loads(payload)

		record["argv"] = command_info.get("argv")
		record["cwd"] = command_info.get("cwd")
		record["pid"] = command_info.get("pid")
		record["env_keys"] = command_info.get("env_keys")
		record["stdio"] = command_info.get("stdio")
		stdout.tty = bool(command_info.get("stdout_tty"))
		stderr.tty = bool(command_info.get("stderr_tty"))
		shim_clock["info"] = command_info.get("t_info")
		for key in ("ppid", "pgid", "sid", "start_ticks", "ancestry"):
			record[key] = command_info.get(key)
		PROCESS_TREE.link(record, command_info.get("ancestry"))

		# pre-command state record; off the loop, since monitors touch no broker state
		await asyncio.to_thread(run_before_command, context)

		decision = {
			"command_id": command_id,
			"allow": True,
			"reason": "",
			"exit_code": 126,
			"subreaper": SUBREAPER,
		}

		context.state["released"] = (time.monotonic(), datetime.now(timezone.utc))
		await send_frame(
			writer,
			DECISION,
			json.dumps(decision).encode("utf-8"),
		)

		# Read command output until the shell shim reports completion or disconnects
		while True:
			frame = await read_frame(reader)

			if frame is None:
				# Shim process disappeared before END marker
				break

			message_type, payload = frame

			if message_type == STARTED:
				started = json.loads(payload)
				record["child_pid"] = started.get("child_pid")
				record["child_start_ticks"] = started.get("child_start_ticks")
				shim_clock["spawn"] = started.get("t_spawn")
				PROCESS_TREE.add(
					started.get("child_pid"), started.get("child_start_ticks"), command_id, "child"
				)
				continue

			if message_type == STDOUT:
				stdout.write(payload)
				continue

			if message_type == STDERR:
				stderr.write(payload)
				continue

			# Record the command outcome since END marker observed
			# and also label session complete
			if message_type == END:
				end = json.loads(payload)
				context.state["ended"] = (time.monotonic(), datetime.now(timezone.utc))
				record["end_reason"] = "end_frame"
				shim_clock["exit"] = end.get("t_exit")
				orphans = end.get("orphans")
				record["orphans"] = orphans
				for orphan in orphans or []:
					owner = PROCESS_TREE.add(
						orphan.get("pid"), orphan.get("start_ticks"), command_id, "orphan"
					)
					if owner is not None and owner[0] != command_id:
						# Nested subreapers list the same process; the first keeps it.
						if owner[1] == "orphan":
							orphan["inherited_from"] = owner[0]
						else:
							orphan["command_id"] = owner[0]

				exit_code = end.get("exit_code")
				signal = end.get("signal")

				record["exit_code"] = exit_code
				record["signal"] = signal

				if exit_code is not None:
					record["return_code"] = exit_code

				elif signal is not None:
					record["return_code"] = 128 + signal

				record["complete"] = True
				break

	except Exception as exc:
		record["monitor_error"] = str(exc)

	finally:
		if "ended" not in context.state:
			# Timed to the disconnect, so a killed shim still has a duration.
			context.state["ended"] = (time.monotonic(), datetime.now(timezone.utc))
			record["end_reason"] = "disconnect"
		PROCESS_TREE.close(command_id)
		stdout.close()
		stderr.close()

		record["stdout"] = stdout.summary(output_dir)
		record["stderr"] = stderr.summary(output_dir)

		# After commands are run; off the loop, so a slow snapshot holds no other shell
		await asyncio.to_thread(run_after_command, context)

		write_record(record, log_path)
		writer.close()
		try:
			await writer.wait_closed()
		except OSError:
			pass


async def serve(
	output_dir: str,
	workspace_dir: str,
	socket_path: str = SOCKET_PATH,
	sock: socketlib.socket | None = None,
	snapshotter: WorkdirSnapshotter | None = None,
) -> int:
	"""Serves the broker, on <sock> when an embedder already bound one.

	Runs until SIGTERM. A snapshotting broker then takes the final workspace
	snapshot, and the exit status says whether it succeeded.
	"""
	os.makedirs(output_dir, exist_ok=True)
	log_path = os.path.join(output_dir, LOG_BASENAME)
	run_id = os.path.basename(os.path.normpath(output_dir))

	if sock is None:
		# Only when this process owns the path. Unlinking a socket an embedder
		# bound would leave its clients connecting to nothing.
		try:
			os.unlink(socket_path)
		except FileNotFoundError:
			pass
	register(CommandTiming())
	register(FileSnapshot(workspace_dir, os.path.join(output_dir, TRACKED_FILES_BASENAME)))

	# asyncio schedules one handler task per connection
	async def handle_connection(
		reader: asyncio.StreamReader,
		writer: asyncio.StreamWriter,
	) -> None:
		await handle_client(reader, writer, output_dir, run_id, snapshotter)

	terminate = asyncio.Event()
	asyncio.get_running_loop().add_signal_handler(signallib.SIGTERM, terminate.set)
	if snapshotter is not None:
		snapshotter.start()

	if sock is None:
		server = await asyncio.start_unix_server(handle_connection, path=socket_path)
	else:
		server = await asyncio.start_unix_server(handle_connection, sock=sock)

	print(f"listening on {socket_path}")
	print(f"journal at {log_path}")
	if snapshotter is not None:
		print(f"workdir snapshots of {snapshotter.subvolume} in {snapshotter.snapshot_dir}")
	sys.stdout.flush()

	# One broker remains available for the duration of the agent invocation.
	try:
		await terminate.wait()
	finally:
		# Not wait_closed(): it would wait on shells the agent left running.
		server.close()

	if snapshotter is None:
		return 0
	# The agent has exited, so this snapshot holds what its last tool call did.
	final = await snapshotter.request({"kind": "final"})
	snapshotter.stop()
	outcome = "ok" if final["ok"] else final["error"]
	print(f"final snapshot {final['seq']}: {outcome}", flush=True)
	return 0 if final["ok"] else EXIT_FINAL_SNAPSHOT_FAILED


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(prog="monitor.py")
	parser.add_argument("output_dir")
	parser.add_argument("workspace_dir")
	parser.add_argument("socket_path", nargs="?", default=SOCKET_PATH)
	# An embedder passes an already-listening descriptor, so the socket is
	# accepting before this process starts.
	parser.add_argument("socket_fd", nargs="?", type=int, default=None)
	parser.add_argument(
		"--snapshot-subvolume",
		default=None,
		help="Btrfs subvolume holding the workspace; enables workdir snapshot requests.",
	)
	parser.add_argument(
		"--snapshot-dir",
		default=None,
		help="Where snapshots go; on the same btrfs filesystem as the subvolume.",
	)
	args = parser.parse_args(argv)
	if (args.snapshot_subvolume is None) != (args.snapshot_dir is None):
		parser.error("--snapshot-subvolume and --snapshot-dir go together")

	sock = None if args.socket_fd is None else socketlib.socket(fileno=args.socket_fd)
	snapshotter = (
		None
		if args.snapshot_subvolume is None
		else WorkdirSnapshotter(
			args.snapshot_subvolume,
			args.snapshot_dir,
			os.path.join(args.output_dir, WORKDIR_SNAPSHOTS_BASENAME),
		)
	)
	return asyncio.run(
		serve(args.output_dir, args.workspace_dir, args.socket_path, sock, snapshotter)
	)


if __name__ == "__main__":
	sys.exit(main())
