"""Claude Code PreToolUse hook: snapshot the workspace before the tool runs.

Installed by AEBench for `case monitor --workdir-snapshots`. It reads the hook
input from stdin, sends the tool call's metadata (never its input) to the
command broker as a workdir snapshot request, and returns once the snapshot is
taken, so the snapshot shows the workspace exactly as the tool found it.

Fails closed: when the broker cannot be reached or the snapshot fails, the
hook denies the tool call and stops the agent, so no tool runs unsnapshotted.

	python3 workdir_snapshot_hook.py [socket-path] [timeout-seconds]
"""

from __future__ import annotations

import json
import socket
import struct
import sys

SOCKET_PATH = "/run/aebench/command.sock"
TIMEOUT_SECONDS = 60.0
# The broker's frame types (shim/src/monitor.py).
WORKDIR_SNAPSHOT = 5
WORKDIR_SNAPSHOT_DONE = 6
HEADER = struct.Struct(">IB")
FIELDS = ("tool_use_id", "tool_name", "session_id", "agent_id", "agent_type")


def main(argv: list[str]) -> int:
	socket_path = argv[1] if len(argv) > 1 else SOCKET_PATH
	timeout = float(argv[2]) if len(argv) > 2 else TIMEOUT_SECONDS
	try:
		event = json.load(sys.stdin)
		request = {key: event.get(key) for key in FIELDS if event.get(key) is not None}
		request["kind"] = "tool"
		reply = _request_snapshot(socket_path, request, timeout)
	except Exception as exc:
		return _stop(f"{type(exc).__name__}: {exc}")
	if not reply.get("ok"):
		return _stop(str(reply.get("error")))
	return 0


def _request_snapshot(
	socket_path: str, request: dict[str, object], timeout: float
) -> dict[str, object]:
	payload = json.dumps(request).encode("utf-8")
	with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
		client.settimeout(timeout)
		client.connect(socket_path)
		client.sendall(HEADER.pack(len(payload), WORKDIR_SNAPSHOT) + payload)
		length, frame_type = HEADER.unpack(_recv_exactly(client, HEADER.size))
		body = _recv_exactly(client, length)
	if frame_type != WORKDIR_SNAPSHOT_DONE:
		raise RuntimeError(f"unexpected reply frame {frame_type}")
	reply = json.loads(body)
	if not isinstance(reply, dict):
		raise RuntimeError("malformed reply")
	return reply


def _recv_exactly(client: socket.socket, size: int) -> bytes:
	data = bytearray()
	while len(data) < size:
		chunk = client.recv(size - len(data))
		if not chunk:
			raise RuntimeError("the broker closed the connection")
		data += chunk
	return bytes(data)


def _stop(reason: str) -> int:
	message = f"AEBench workdir snapshot failed, so the run is stopped: {reason}"
	print(message, file=sys.stderr)
	# Deny this call, and end the session: no tool may run without its snapshot.
	json.dump(
		{
			"continue": False,
			"stopReason": message,
			"hookSpecificOutput": {
				"hookEventName": "PreToolUse",
				"permissionDecision": "deny",
				"permissionDecisionReason": message,
			},
		},
		sys.stdout,
	)
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
