"""Command monitoring: a shell shim reports every invocation to a local broker.

The shim executes; the broker decides and records. See ``docs/`` and issue #90
for why the data path stays inside the agent's process tree.
"""

from __future__ import annotations

from .broker import DEFAULT_BACKLOG, SOCKET_BASENAME, CommandBroker
from .interceptors import PatternDenyPolicy, RecordCollector
from .protocol import (
	DecisionMessage,
	EndMessage,
	Frame,
	FrameKind,
	ProtocolError,
	decode_control,
	encode_begin,
	encode_control,
	encode_frame,
	read_control,
	read_frame,
)
from .runner import BaseInterceptor, CommandRunner, Verdict
from .trace import (
	CAPTURE_DIRNAME,
	TRACE_BASENAME,
	CaptureSink,
	CommandTraceWriter,
	iter_command_trace,
	read_command_trace,
)
from .types import (
	DENIED_EXIT_CODE,
	CaptureState,
	CommandOutcome,
	CommandRecord,
	CommandRequest,
	Decision,
)

__all__ = [
	"CAPTURE_DIRNAME",
	"DEFAULT_BACKLOG",
	"DENIED_EXIT_CODE",
	"SOCKET_BASENAME",
	"TRACE_BASENAME",
	"BaseInterceptor",
	"CaptureSink",
	"CaptureState",
	"CommandBroker",
	"CommandOutcome",
	"CommandRecord",
	"CommandRequest",
	"CommandRunner",
	"CommandTraceWriter",
	"Decision",
	"DecisionMessage",
	"EndMessage",
	"Frame",
	"FrameKind",
	"PatternDenyPolicy",
	"ProtocolError",
	"RecordCollector",
	"Verdict",
	"decode_control",
	"encode_begin",
	"encode_control",
	"encode_frame",
	"iter_command_trace",
	"read_command_trace",
	"read_control",
	"read_frame",
]
