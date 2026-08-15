import json
import os
import socket
import struct
import uuid

SOCKET_PATH = "/run/aebench/command.sock"
LOG_PATH = "/tmp/commands.jsonl"

COMMAND_INFO = 0
STDOUT = 1
STDERR = 2
END = 3
DECISION = 4


def read_buffer(sock, size):
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            if not data:
                return None

            raise EOFError("connection ended mid-frame")

        data.extend(chunk)

    return bytes(data)


def read_frame(sock):
    header = read_buffer(sock, 5)

    if header is None:
        return None

    length = struct.unpack(">I", header[:4])[0]
    kind = header[4]

    payload = read_buffer(sock, length)

    if payload is None and length:
        raise EOFError("connection ended mid-frame")

    return kind, payload or b""


def send_frame(sock, kind, payload):
    header = struct.pack(">I", len(payload)) + bytes([kind])

    sock.sendall(header + payload)


def write_record(record):
    with open(LOG_PATH, "a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")


def handle_connection(connection):
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

        kind, payload = first

        if kind != COMMAND_INFO:
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

        while True:
            frame = read_frame(connection)

            if frame is None:
                break

            kind, payload = frame

            if kind in (STDOUT, STDERR):
                continue

            if kind == END:
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


def main():
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

        try:
            handle_connection(connection)
        finally:
            connection.close()


if __name__ == "__main__":
    main()