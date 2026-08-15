import json
import os
import socket
import struct
import threading
import uuid
from typing import TypeAlias

Frame: TypeAlias = tuple[int, bytes]
Record: TypeAlias = dict[str, object]

SOCKET_PATH = "/run/aebench/command.sock"
LOG_PATH = "/tmp/commands.jsonl"

MAX_FRAME_BYTES = 16 * 1024 * 1024

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


def write_record(record: Record) -> None:
    with log_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")


def process_connection(connection: socket.socket) -> None:
    """Process and monitor the session for one shell invocation."""
    command_id = uuid.uuid4().hex

    record = {
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
        record["pid"] = command_info.get("pid")

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
                # Do not store output yet; wait for END marker
                continue

            if message_type == STDERR:
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
        write_record(record)
        connection.close()


def main() -> None:
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen()

    print(f"listening on {SOCKET_PATH}")

    while True:
        connection, _ = server.accept()

        threading.Thread(
            target=process_connection,
            args=(connection,),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
