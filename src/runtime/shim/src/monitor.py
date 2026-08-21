import json
import os
import socket
import struct
import sys
import threading
import uuid
from typing import BinaryIO, TypeAlias

Frame: TypeAlias = tuple[int, bytes]
Record: TypeAlias = dict[str, object]

SOCKET_PATH = "/run/aebench/command.sock"
LOG_BASENAME = "commands.jsonl"

STREAMS_DIRNAME = "stream"

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024

COMMAND_INFO = 0
STDOUT = 1
STDERR = 2
END = 3
DECISION = 4

log_lock = threading.Lock()


def read_buffer(sock: socket.socket, size: int) -> bytes | None:
	"""Reads exactly <size> bytes from a shell shim stream socket."""
	data = bytearray()

	while len(data) < size:
		chunk = sock.recv(size - len(data))

		if not chunk:
			if not data:
				return None

			raise EOFError("connection ended mid-frame")

		data.extend(chunk)

	return bytes(data)


def read_frame(sock: socket.socket) -> Frame | None:
	"""Read one complete message from a shell shim socket."""
	header = read_buffer(sock, 5)

	if header is None:
		return None

	length = struct.unpack(">I", header[:4])[0]
	message_type = header[4]

	if length > MAX_FRAME_BYTES:
		raise ValueError("frame too large")

	payload = read_buffer(sock, length)

	if payload is None and length:
		raise EOFError("connection ended mid-frame")

	return message_type, payload or b""


def send_frame(
	sock: socket.socket,
	message_type: int,
	payload: bytes,
) -> None:
	header = struct.pack(">I", len(payload)) + bytes([message_type])

	sock.sendall(header + payload)


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


def write_record(record: Record, log_path: str) -> None:
	with log_lock:
		with open(log_path, "a", encoding="utf-8") as file:
			file.write(json.dumps(record) + "\n")


def process_connection(connection: socket.socket, output_dir: str, run_id: str) -> None:
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

	try:
		first = read_frame(connection)

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

		decision = {
			"command_id": command_id,
			"allow": True,
			"reason": "",
			"exit_code": 126,
		}

		send_frame(
			connection,
			DECISION,
			json.dumps(decision).encode("utf-8"),
		)

		# Read command output until the shell shim reports completion or disconnects
		while True:
			frame = read_frame(connection)

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

		write_record(record, log_path)
		connection.close()


def main(output_dir: str) -> None:
	os.makedirs(output_dir, exist_ok=True)
	log_path = os.path.join(output_dir, LOG_BASENAME)
	run_id = os.path.basename(os.path.normpath(output_dir))

	try:
		os.unlink(SOCKET_PATH)
	except FileNotFoundError:
		pass

	server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	server.bind(SOCKET_PATH)
	server.listen()

	print(f"listening on {SOCKET_PATH}")
	print(f"journal at {log_path}")

	while True:
		connection, _ = server.accept()

		threading.Thread(
			target=process_connection,
			args=(connection, output_dir, run_id),
			daemon=True,
		).start()


if __name__ == "__main__":
	if len(sys.argv) != 2:
		raise SystemExit("usage: monitor.py <run-output-dir>")

	main(sys.argv[1])
