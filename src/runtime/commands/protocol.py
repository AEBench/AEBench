"""Wire protocol between the shell shim and the command broker.

Every message is a length-prefixed frame::

	u32 payload_length (big endian) | u8 frame_kind | payload

Control frames carry a UTF-8 JSON object with a ``type`` discriminator. Output
frames carry raw bytes, so binary command output needs no escaping and a stream
larger than one frame is simply sent as several.

One command is one connection::

	shim -> begin       argv, cwd, pid, environment variable names
	     <- decision    command id, and whether it may run
	shim -> stdout/stderr frames
	shim -> end         exit code and signal

Output is not streamed. The agent reads it live from the shim's own
descriptors, which is the only place it is needed in real time, so the shim
sends both streams once the command is over. Nothing comes back after the
decision: the connection identifies the command, and bytes the shim has already
written arrive whether or not it has exited by the time they are read.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import IO, Any, Mapping, Sequence

from .types import CommandRequest, argv_tuple

_HEADER = struct.Struct(">IB")

#: Refuse absurd frame lengths rather than allocating on a corrupt header.
MAX_FRAME_BYTES = 16 * 1024 * 1024


class ProtocolError(RuntimeError):
	"""Raised when a peer sends a malformed or unexpected message."""


class FrameKind(IntEnum):
	"""Frame discriminator."""

	CONTROL = 0
	STDOUT = 1
	STDERR = 2


@dataclass(frozen=True, slots=True)
class Frame:
	"""One decoded frame."""

	kind: FrameKind
	payload: bytes


@dataclass(frozen=True, slots=True)
class DecisionMessage:
	"""Broker replies with the verdict and the assigned command id."""

	command_id: str
	allow: bool = True
	reason: str = ""
	exit_code: int = 126

	type_name = "decision"

	def to_dict(self) -> dict[str, Any]:
		"""Returns the JSON payload for this message."""
		return {
			"type": self.type_name,
			"command_id": self.command_id,
			"allow": self.allow,
			"reason": self.reason,
			"exit_code": self.exit_code,
		}

	@classmethod
	def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionMessage":
		"""Parses a ``decision`` payload."""
		return cls(
			command_id=_require_str(payload, "command_id"),
			allow=bool(payload.get("allow", True)),
			reason=str(payload.get("reason", "")),
			exit_code=int(payload.get("exit_code", 126)),
		)


@dataclass(frozen=True, slots=True)
class EndMessage:
	"""Shim reports how the command finished.

	No command id: the connection already identifies the command. No duration
	or byte counts: the broker times the command and counts what it read.
	"""

	exit_code: int | None = None
	signal: int | None = None

	type_name = "end"

	def to_dict(self) -> dict[str, Any]:
		"""Returns the JSON payload for this message."""
		return {
			"type": self.type_name,
			"exit_code": self.exit_code,
			"signal": self.signal,
		}

	@classmethod
	def from_dict(cls, payload: Mapping[str, Any]) -> "EndMessage":
		"""Parses an ``end`` payload."""
		return cls(
			exit_code=_optional_int(payload, "exit_code"),
			signal=_optional_int(payload, "signal"),
		)


#: Everything a control frame can decode to. A begin frame becomes the request
#: the broker reasons about directly, so there is no separate begin message.
ControlMessage = CommandRequest | DecisionMessage | EndMessage

#: The messages with a JSON body of their own; begin is encoded by encode_begin.
EncodableMessage = DecisionMessage | EndMessage

BEGIN_TYPE = "begin"


def encode_begin(request: CommandRequest) -> bytes:
	"""Encodes a begin frame. The shim writes this shape from Rust."""
	body = json.dumps(
		{
			"type": BEGIN_TYPE,
			"argv": list(request.argv),
			"cwd": request.cwd,
			"pid": request.pid,
			"env_keys": list(request.env_keys),
		},
		separators=(",", ":"),
	).encode("utf-8")
	return encode_frame(FrameKind.CONTROL, body)


def _begin_from_dict(payload: Mapping[str, Any]) -> CommandRequest:
	"""Parses a ``begin`` payload into the request the broker reasons about."""
	return CommandRequest(
		argv=argv_tuple(_require_str_list(payload, "argv")),
		cwd=_require_str(payload, "cwd"),
		pid=_optional_int(payload, "pid"),
		env_keys=tuple(payload.get("env_keys") or ()),
	)


_CONTROL_DECODERS: dict[str, Any] = {
	BEGIN_TYPE: _begin_from_dict,
	DecisionMessage.type_name: DecisionMessage.from_dict,
	EndMessage.type_name: EndMessage.from_dict,
}


def encode_frame(kind: FrameKind, payload: bytes) -> bytes:
	"""Encodes one frame."""
	if len(payload) > MAX_FRAME_BYTES:
		raise ProtocolError(f"frame payload too large: {len(payload)} bytes")
	return _HEADER.pack(len(payload), int(kind)) + payload


def encode_control(message: EncodableMessage) -> bytes:
	"""Encodes a control message as a frame."""
	body = json.dumps(message.to_dict(), separators=(",", ":")).encode("utf-8")
	return encode_frame(FrameKind.CONTROL, body)


def read_frame(reader: IO[bytes]) -> Frame | None:
	"""Reads one frame, or returns ``None`` at a clean end of stream."""
	header = _read_exactly(reader, _HEADER.size)
	if header is None:
		return None

	length, raw_kind = _HEADER.unpack(header)
	if length > MAX_FRAME_BYTES:
		raise ProtocolError(f"frame length {length} exceeds the {MAX_FRAME_BYTES} byte limit")
	try:
		kind = FrameKind(raw_kind)
	except ValueError as exc:
		raise ProtocolError(f"unknown frame kind: {raw_kind}") from exc

	payload = b"" if length == 0 else _read_exactly(reader, length)
	if payload is None:
		raise ProtocolError("stream ended mid-frame")
	return Frame(kind=kind, payload=payload)


def decode_control(payload: bytes) -> ControlMessage:
	"""Decodes a control frame payload into a message."""
	try:
		data = json.loads(payload.decode("utf-8"))
	except (UnicodeDecodeError, json.JSONDecodeError) as exc:
		raise ProtocolError(f"control frame is not valid JSON: {exc}") from exc
	if not isinstance(data, dict):
		raise ProtocolError("control frame must be a JSON object")

	message_type = data.get("type")
	decoder = _CONTROL_DECODERS.get(message_type) if isinstance(message_type, str) else None
	if decoder is None:
		raise ProtocolError(f"unknown control message type: {message_type!r}")
	decoded: ControlMessage = decoder(data)
	return decoded


def read_control(reader: IO[bytes]) -> ControlMessage | None:
	"""Reads the next frame and requires it to be a control message."""
	frame = read_frame(reader)
	if frame is None:
		return None
	if frame.kind is not FrameKind.CONTROL:
		raise ProtocolError(f"expected a control frame, got {frame.kind.name}")
	return decode_control(frame.payload)


def _read_exactly(reader: IO[bytes], count: int) -> bytes | None:
	"""Reads exactly ``count`` bytes, or ``None`` if the stream ends first."""
	chunks: list[bytes] = []
	remaining = count
	while remaining > 0:
		chunk = reader.read(remaining)
		if not chunk:
			return None
		chunks.append(chunk)
		remaining -= len(chunk)
	return b"".join(chunks)


def _require_str(payload: Mapping[str, Any], key: str) -> str:
	value = payload.get(key)
	if not isinstance(value, str):
		raise ProtocolError(f"{key!r} must be a string")
	return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
	value = payload.get(key)
	if value is None or isinstance(value, str):
		return value
	raise ProtocolError(f"{key!r} must be a string or null")


def _optional_int(payload: Mapping[str, Any], key: str) -> int | None:
	value = payload.get(key)
	if value is None:
		return None
	if isinstance(value, bool) or not isinstance(value, int):
		raise ProtocolError(f"{key!r} must be an integer or null")
	return int(value)


def _require_str_list(payload: Mapping[str, Any], key: str) -> Sequence[str]:
	value = payload.get(key)
	if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
		raise ProtocolError(f"{key!r} must be a list of strings")
	return value


__all__ = [
	"BEGIN_TYPE",
	"MAX_FRAME_BYTES",
	"ControlMessage",
	"EncodableMessage",
	"DecisionMessage",
	"EndMessage",
	"Frame",
	"FrameKind",
	"ProtocolError",
	"decode_control",
	"encode_begin",
	"encode_control",
	"encode_frame",
	"read_control",
	"read_frame",
]
