import json
import os
import socket
import struct

SOCKET_PATH = "/run/aebench/command.sock"

COMMAND_INFO = 0
STDOUT = 1
STDERR = 2
END = 3
DECISION = 4


def read_buffer(sock, size):
    data = b""

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            return None

        data += chunk

    return data


def read_frame(sock):
    header = read_buffer(sock, 5)

    if header is None:
        return None

    length = struct.unpack(">I", header[:4])[0]
    kind = header[4]

    payload = read_buffer(sock, length)

    return kind, payload


def send_frame(sock, kind, payload):
    header = struct.pack(">I", len(payload)) + bytes([kind])

    sock.sendall(header + payload)


def handle_connection(connection):
    frame = read_frame(connection)

    if frame is None:
        return

    kind, payload = frame

    if kind != COMMAND_INFO:
        raise ValueError("expected command_info")

    command = json.loads(payload)

    print("running:", command["argv"])

    decision = {
        "command_id": "1",
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
            print("connection closed before end")
            return

        kind, payload = frame

        if kind == STDOUT:
            continue

        if kind == STDERR:
            continue

        if kind == END:
            result = json.loads(payload)

            print(
                "finished:",
                command["argv"],
                result,
            )

            return


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