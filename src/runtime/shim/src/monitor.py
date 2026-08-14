import os
import socket

SOCKET_PATH = "/run/aebench/command.sock"


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

        print("aeshell connected")

        connection.close()


if __name__ == "__main__":
    main()