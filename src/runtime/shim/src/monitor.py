import asyncio
import hashlib
import json
import os
import socket as socketlib
import stat as statlib
import struct
import sys
import time
import uuid
from collections.abc import Iterator
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
MAX_UNTRACKED_FILES = 100_000  # Memory guard for the untracked walk
COLLAPSE_UNTRACKED_FILES = 2_000  # Untracked-only folders above this are summarised
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
		# Every folder holding a tracked file, at any depth
		self.tracked_dirs = frozenset(
			parent for rel in self.tracked_set for parent in _parents(rel)
		)

	def _snapshot(self) -> tuple[dict[str, FileStat] | None, str | None, str | None]:
		"""(picture, reason it is None, reason its untracked part is unknown)."""
		if self.tracked is not None:
			return self._snapshot_tracked()
		found, reason = self._snapshot_walk()
		return found, reason, None

	def _snapshot_tracked(self) -> tuple[dict[str, FileStat] | None, str | None, str | None]:
		"""Maps each tracked file still present, then every untracked one, to its FileStat."""
		assert self.tracked is not None
		found: dict[str, FileStat] = {}
		deadline = time.monotonic() + SNAPSHOT_BUDGET_SECONDS

		for rel in self.tracked:
			if time.monotonic() >= deadline:
				return None, f"snapshot exceeded {SNAPSHOT_BUDGET_SECONDS:g}s", None

			try:
				# Using lstat to tracked symlink is the link itself.
				stat = os.lstat(self.workspace / rel)
			except OSError:
				# The tracked file not found, so reported as deleted.
				continue

			found[rel] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)

		untracked, reason = self._walk_untracked(deadline)
		if untracked is None:
			# The tracked part stays exact; only the untracked part is unknown.
			return found, None, reason
		found.update(untracked)
		return found, None, None

	def _walk_untracked(self, deadline: float) -> tuple[dict[str, FileStat] | None, str | None]:
		"""Maps the files outside the tracked list to their FileStat, skipping .git."""
		found: dict[str, FileStat] = {}
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
					return None, f"untracked walk exceeded {SNAPSHOT_BUDGET_SECONDS:g}s"

				path = os.path.join(directory, filename)
				rel = os.path.relpath(path, self.workspace)
				if rel in self.tracked_set:
					continue
				try:
					stat = os.lstat(path)
				except OSError:
					continue

				found[rel] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
				if len(found) > MAX_UNTRACKED_FILES:
					return None, f"workspace exceeds {MAX_UNTRACKED_FILES} untracked files"

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

		before, before_reason, before_untracked = ctx.state[self.name]
		after, after_reason, after_untracked = self._snapshot()

		if before is None or after is None:
			# Explains reason for skipped
			return {"skipped": before_reason or after_reason}

		created = sorted(set(after) - set(before))
		deleted = sorted(set(before) - set(after))
		modified = sorted(name for name in set(before) & set(after) if before[name] != after[name])

		untracked_skipped = before_untracked or after_untracked
		collapsed: list[Record] | None = None
		if self.tracked is None:
			result: Record = {"scope": "workspace"}
			lists: dict[str, list[str] | None] = {
				"created": created,
				"modified": modified,
				"deleted": deleted,
			}
		else:
			# Untracked paths go only in the *_untracked lists, so created,
			# modified and deleted keep meaning tracked files, as the checker reads them.
			result = {"scope": "tracked+untracked"}
			lists = {}
			changes = {"created": created, "modified": modified, "deleted": deleted}
			for op, names in changes.items():
				lists[op] = [name for name in names if name in self.tracked_set]
			if untracked_skipped is None:
				untracked = {
					op: [name for name in names if name not in self.tracked_set]
					for op, names in changes.items()
				}
				collapsed = self._collapse(untracked, before, after)
			for op in changes:
				lists[f"{op}_untracked"] = None if untracked_skipped else untracked[op]

		listed = {
			op: None if names is None else names[:MAX_LISTED_PATHS] for op, names in lists.items()
		}
		changed = [
			name
			for op in ("created", "modified", "created_untracked", "modified_untracked")
			for name in listed.get(op) or ()
		]
		hashes, hash_skipped = self._hashes(changed)

		result["watched"] = len(after)
		for op, paths in lists.items():
			result[f"total_{op}"] = None if paths is None else len(paths)
		result.update(listed)
		if self.tracked is not None:
			result["collapsed_untracked"] = collapsed
			result["untracked_skipped"] = untracked_skipped
		result["truncated"] = any(
			names is not None and len(names) > MAX_LISTED_PATHS for names in lists.values()
		)
		result["sizes"] = {name: after[name][0] for name in changed}
		result["hashes"] = hashes
		result["hash_skipped"] = hash_skipped
		return result

	def _collapse(
		self,
		untracked: dict[str, list[str]],
		before: dict[str, FileStat],
		after: dict[str, FileStat],
	) -> list[Record]:
		"""Moves changes under big untracked-only folders out of `untracked` into summaries.

		A folder holding a tracked file is never summarised; its changes stay listed.
		"""
		candidates = {
			parent
			for names in untracked.values()
			for name in names
			for parent in _parents(name)
			if parent not in self.tracked_dirs
		}
		if not candidates:
			return []

		after_counts = self._dir_counts(after, candidates)
		counts = dict(after_counts)
		if untracked["deleted"]:
			# A folder emptied by this command is sized by what it held before.
			for parent, count in self._dir_counts(before, candidates).items():
				counts[parent] = max(counts.get(parent, 0), count)
		big = {parent for parent, count in counts.items() if count > COLLAPSE_UNTRACKED_FILES}
		if not big:
			return []

		summaries: dict[str, dict[str, int]] = {}
		for op, names in untracked.items():
			kept = []
			for name in names:
				# Parents come shortest first, so this is the topmost big folder.
				top = next((parent for parent in _parents(name) if parent in big), None)
				if top is None:
					kept.append(name)
					continue
				summary = summaries.setdefault(top, {"created": 0, "modified": 0, "deleted": 0})
				summary[op] += 1
			names[:] = kept
		return [
			{"dir": top, "files": after_counts.get(top, 0), **summaries[top]}
			for top in sorted(summaries)
		]

	def _dir_counts(self, picture: dict[str, FileStat], dirs: set[str]) -> dict[str, int]:
		"""Untracked files in `picture` under each of `dirs`, recursively."""
		counts: dict[str, int] = {}
		for name in picture:
			if name in self.tracked_set:
				continue
			for parent in _parents(name):
				if parent in dirs:
					counts[parent] = counts.get(parent, 0) + 1
		return counts


def _parents(rel: str) -> Iterator[str]:
	"""Each folder above a workspace-relative path, shortest first, never the root."""
	cut = rel.find("/")
	while cut != -1:
		yield rel[:cut]
		cut = rel.find("/", cut + 1)


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


def write_record(record: Record, log_path: str) -> None:
	with open(log_path, "a", encoding="utf-8") as file:
		file.write(json.dumps(record) + "\n")


async def process_connection(
	reader: asyncio.StreamReader,
	writer: asyncio.StreamWriter,
	output_dir: str,
	run_id: str,
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
) -> None:
	"""Serves the broker, on <sock> when an embedder already bound one."""
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
		await process_connection(reader, writer, output_dir, run_id)

	if sock is None:
		server = await asyncio.start_unix_server(handle_connection, path=socket_path)
	else:
		server = await asyncio.start_unix_server(handle_connection, sock=sock)

	print(f"listening on {socket_path}")
	print(f"journal at {log_path}")

	# One broker remains available for the duration of the agent invocation.
	async with server:
		await server.serve_forever()


def main(
	output_dir: str,
	workspace_dir: str,
	socket_path: str = SOCKET_PATH,
	socket_fd: str | None = None,
) -> None:
	# An embedder passes an already-listening descriptor, so the socket is
	# accepting before this process starts.
	sock = None if socket_fd is None else socketlib.socket(fileno=int(socket_fd))
	asyncio.run(serve(output_dir, workspace_dir, socket_path, sock))


if __name__ == "__main__":
	if not 3 <= len(sys.argv) <= 5:
		raise SystemExit(
			"usage: monitor.py <run-output-dir> <workspace-dir> [socket-path] [socket-fd]"
		)

	main(*sys.argv[1:])
