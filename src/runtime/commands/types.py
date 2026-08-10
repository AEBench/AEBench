"""Value types for the command-monitoring layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Sequence

#: Exit code reported to the agent when an interceptor denies a command.
DENIED_EXIT_CODE = 126


class CaptureState(str, Enum):
	"""Whether a stream reached the trace.

	A stream the shim left attached to a terminal is indistinguishable here from
	one that produced nothing: both arrive as no bytes at all.
	"""

	CAPTURED = "captured"
	NOT_CAPTURED = "not_captured"


@dataclass(frozen=True, slots=True)
class CommandRequest:
	"""One shell invocation as observed before execution.

	Environment *values* never cross the wire: the shim reports variable names
	only, so credentials in the agent's environment stay out of the trace.

	``parent_command_id`` is filled in by the broker from the process tree, not
	reported by the shim.
	"""

	argv: tuple[str, ...]
	cwd: str
	pid: int | None = None
	parent_command_id: str | None = None
	env_keys: tuple[str, ...] = ()

	@property
	def shell_source(self) -> str | None:
		"""Returns the ``-c`` command string, when this is a ``bash -c`` call."""
		return shell_source_from_argv(self.argv)


@dataclass(frozen=True, slots=True)
class Decision:
	"""The server's verdict on a shell invocation."""

	allow: bool = True
	reason: str = ""
	exit_code: int = DENIED_EXIT_CODE

	@classmethod
	def permit(cls) -> "Decision":
		"""Returns an allow verdict."""
		return cls()

	@classmethod
	def deny(cls, reason: str, *, exit_code: int = DENIED_EXIT_CODE) -> "Decision":
		"""Returns a deny verdict carrying an agent-visible reason."""
		if not reason.strip():
			raise ValueError("a deny decision requires a reason")
		return cls(allow=False, reason=reason, exit_code=exit_code)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
	"""What happened when a shell invocation ran."""

	exit_code: int | None = None
	signal: int | None = None
	duration_ms: int = 0
	stdout_state: CaptureState = CaptureState.NOT_CAPTURED
	stderr_state: CaptureState = CaptureState.NOT_CAPTURED

	@classmethod
	def denied(cls, decision: Decision) -> "CommandOutcome":
		"""Returns the outcome recorded for a command that never ran."""
		return cls(exit_code=decision.exit_code)


@dataclass(frozen=True, slots=True)
class CommandRecord:
	"""One line of ``commands.jsonl``."""

	command_id: str
	request: CommandRequest
	decision: Decision
	outcome: CommandOutcome
	started_at: datetime
	finished_at: datetime | None = None
	stdout_path: str | None = None
	stderr_path: str | None = None
	denied_by: str | None = None
	incomplete: bool = False

	def to_json_dict(self) -> dict[str, Any]:
		"""Returns the JSON-serializable form written to the trace."""
		return {
			"command_id": self.command_id,
			"parent_command_id": self.request.parent_command_id,
			"argv": list(self.request.argv),
			"shell_source": self.request.shell_source,
			"cwd": self.request.cwd,
			"pid": self.request.pid,
			"env_keys": list(self.request.env_keys),
			"allowed": self.decision.allow,
			"denied_by": self.denied_by,
			"deny_reason": self.decision.reason or None,
			"started_at": self.started_at.isoformat(),
			"finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
			"duration_ms": self.outcome.duration_ms,
			"exit_code": self.outcome.exit_code,
			"signal": self.outcome.signal,
			"stdout_path": self.stdout_path,
			"stderr_path": self.stderr_path,
			"stdout_state": self.outcome.stdout_state.value,
			"stderr_state": self.outcome.stderr_state.value,
			"incomplete": self.incomplete,
		}


def shell_source_from_argv(argv: Sequence[str]) -> str | None:
	"""Extracts the command string from ``bash -c "..."``, clusters included.

	Derived here rather than reported by the shim, which has nothing to add
	beyond the argv it already sends.
	"""
	index = 1
	while index < len(argv):
		arg = argv[index]
		if arg == "--" or not arg.startswith("-"):
			return None
		if not arg.startswith("--") and "c" in arg:
			return argv[index + 1] if index + 1 < len(argv) else None
		index += 1
	return None


def argv_tuple(argv: Sequence[str]) -> tuple[str, ...]:
	"""Normalizes an argv sequence, rejecting empty vectors."""
	values = tuple(str(item) for item in argv)
	if not values:
		raise ValueError("argv must not be empty")
	return values


__all__ = [
	"DENIED_EXIT_CODE",
	"CaptureState",
	"CommandOutcome",
	"CommandRecord",
	"CommandRequest",
	"Decision",
	"argv_tuple",
	"shell_source_from_argv",
]
