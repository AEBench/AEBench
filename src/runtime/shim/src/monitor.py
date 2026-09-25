import asyncio
import json
import os
import struct
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, TypeAlias

Frame: TypeAlias = tuple[int, bytes]
Record: TypeAlias = dict[str, object]

SOCKET_PATH = "/run/aebench/command.sock"
LOG_BASENAME = "commands.jsonl"

STREAMS_DIRNAME = "stream"

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024

MAX_LISTED_PATHS = 100
MAX_SNAPSHOT_FILES = 20000

# How long snapshot is before timeout. Nothing is failed when this expires. Result is abandoned, the
# file_snapshot entry is logged. The command's record is still written in full.
SNAPSHOT_BUDGET_SECONDS = 2.0

COMMAND_INFO = 0
STDOUT = 1
STDERR = 2
END = 3
DECISION = 4


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
	for monitor in MONITORS:
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
	"""How long each command took. Duration comes from a monotonic clock."""

	name = "timing"

	def before_command(self, ctx: CommandContext) -> None:
		ctx.state[self.name] = (time.monotonic(), datetime.now(timezone.utc))

	def after_command(self, ctx: CommandContext) -> Record | None:
		started = ctx.state.get(self.name)

		if started is None:
			return None

		monotonic_start, wall_start = started

		return {
			"started_at": wall_start.isoformat(),
			"duration_ms": int((time.monotonic() - monotonic_start) * 1000),
			# Wall clock covers everything the command spawned
			"includes_children": True,
		}


class FileSnapshot(Monitor):
	"""What a command changed in the workspace. Walks the whole workspace, so nothing is silently ignored"""

	name = "file_snapshot"

	def __init__(self, workspace: str | Path) -> None:
		self.workspace = Path(workspace)

	def _snapshot(self) -> tuple[dict[str, tuple[int, int]] | None, str | None]:
		"""Maps each file to (size, mtime_ns), or (None, reason) if it gave up."""
		found: dict[str, tuple[int, int]] = {}
		deadline = time.monotonic() + SNAPSHOT_BUDGET_SECONDS

		for directory, _subdirectories, filenames in os.walk(self.workspace):
			for filename in filenames:
				if time.monotonic() >= deadline:
					return None, f"snapshot exceeded {SNAPSHOT_BUDGET_SECONDS:g}s"

				path = os.path.join(directory, filename)

				try:
					stat = os.stat(path)
				except OSError:
					# Vanished mid-walk, or error
					continue

				found[os.path.relpath(path, self.workspace)] = (stat.st_size, stat.st_mtime_ns)

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

		return {
			"watched": len(after),
			"total_created": len(created),
			"total_modified": len(modified),
			"total_deleted": len(deleted),
			"created": created[:MAX_LISTED_PATHS],
			"modified": modified[:MAX_LISTED_PATHS],
			"deleted": deleted[:MAX_LISTED_PATHS],
			"truncated": max(len(created), len(modified), len(deleted)) > MAX_LISTED_PATHS,
			"sizes": {name: after[name][0] for name in (created + modified)[:MAX_LISTED_PATHS]},
		}


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

	record = {
		"run_id": run_id,
		"command_id": command_id,
		"complete": False,
		"exit_code": None,
		"signal": None,
		"return_code": None,
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

		# pre-command state record
		run_before_command(context)

		decision = {
			"command_id": command_id,
			"allow": True,
			"reason": "",
			"exit_code": 126,
		}

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
		stdout.close()
		stderr.close()

		record["stdout"] = stdout.summary(output_dir)
		record["stderr"] = stderr.summary(output_dir)

		# After commands are run
		run_after_command(context)

		write_record(record, log_path)
		writer.close()
		try:
			await writer.wait_closed()
		except OSError:
			pass


async def serve(output_dir: str, workspace_dir: str, socket_path: str = SOCKET_PATH) -> None:
	os.makedirs(output_dir, exist_ok=True)
	log_path = os.path.join(output_dir, LOG_BASENAME)
	run_id = os.path.basename(os.path.normpath(output_dir))

	try:
		os.unlink(socket_path)
	except FileNotFoundError:
		pass
	register(CommandTiming())
	register(FileSnapshot(workspace_dir))

	# asyncio schedules one handler task per connection
	async def handle_connection(
		reader: asyncio.StreamReader,
		writer: asyncio.StreamWriter,
	) -> None:
		await process_connection(reader, writer, output_dir, run_id)

	server = await asyncio.start_unix_server(handle_connection, path=socket_path)

	print(f"listening on {socket_path}")
	print(f"journal at {log_path}")

	# One broker remains available for the duration of the agent invocation.
	async with server:
		await server.serve_forever()


def main(output_dir: str, workspace_dir: str, socket_path: str = SOCKET_PATH) -> None:
	asyncio.run(serve(output_dir, workspace_dir, socket_path))


if __name__ == "__main__":
	if len(sys.argv) not in {3, 4}:
		raise SystemExit("usage: monitor.py <run-output-dir> <workspace-dir> [socket-path]")

	main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else SOCKET_PATH)
