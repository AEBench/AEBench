from __future__ import annotations

import io
import json
import struct

import pytest

from runtime.commands.protocol import (
	MAX_FRAME_BYTES,
	DecisionMessage,
	EndMessage,
	FrameKind,
	ProtocolError,
	decode_control,
	encode_begin,
	encode_control,
	encode_frame,
	read_control,
	read_frame,
)
from runtime.commands.types import CommandRequest


def _reader(*chunks: bytes) -> io.BytesIO:
	return io.BytesIO(b"".join(chunks))


def test_begin_decodes_into_a_command_request() -> None:
	request = CommandRequest(
		argv=("bash", "-lc", "make -j8"), cwd="/repo", pid=41, env_keys=("PATH", "HOME")
	)

	decoded = decode_control(encode_begin(request)[5:])

	assert decoded == request
	assert decoded.shell_source == "make -j8"


@pytest.mark.parametrize(
	"message",
	[
		DecisionMessage(command_id="cmd_000001", allow=False, reason="nope"),
		EndMessage(exit_code=3, signal=None),
	],
)
def test_control_messages_round_trip(message: object) -> None:
	payload = encode_control(message)  # type: ignore[arg-type]

	assert decode_control(payload[5:]) == message


def test_frames_carry_binary_output_unescaped() -> None:
	payload = bytes(range(256))
	stream = _reader(encode_frame(FrameKind.STDOUT, payload))

	frame = read_frame(stream)

	assert frame is not None
	assert frame.kind is FrameKind.STDOUT
	assert frame.payload == payload


def test_read_frame_returns_none_at_clean_eof() -> None:
	assert read_frame(_reader()) is None


def test_read_frame_rejects_a_truncated_frame() -> None:
	full = encode_frame(FrameKind.STDOUT, b"abcdef")

	with pytest.raises(ProtocolError, match="ended mid-frame"):
		read_frame(_reader(full[:-2]))


def test_read_frame_rejects_an_absurd_length() -> None:
	header = struct.pack(">IB", MAX_FRAME_BYTES + 1, int(FrameKind.STDOUT))

	with pytest.raises(ProtocolError, match="exceeds"):
		read_frame(_reader(header))


def test_read_frame_rejects_an_unknown_kind() -> None:
	header = struct.pack(">IB", 0, 99)

	with pytest.raises(ProtocolError, match="unknown frame kind"):
		read_frame(_reader(header))


def test_read_control_rejects_a_data_frame() -> None:
	stream = _reader(encode_frame(FrameKind.STDERR, b"boom"))

	with pytest.raises(ProtocolError, match="expected a control frame"):
		read_control(stream)


def test_decode_control_rejects_an_unknown_message_type() -> None:
	payload = json.dumps({"type": "explode"}).encode()

	with pytest.raises(ProtocolError, match="unknown control message type"):
		decode_control(payload)


def test_decode_control_rejects_a_malformed_begin() -> None:
	payload = json.dumps({"type": "begin", "argv": "make"}).encode()

	with pytest.raises(ProtocolError, match="'argv' must be a list of strings"):
		decode_control(payload)
