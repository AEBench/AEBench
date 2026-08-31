from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuditStatus(str, Enum):
	CLEAN = "clean"
	REVIEW = "review"
	BLOCKED = "blocked"
	ERROR = "error"


class FileEffects(BaseModel):
	"""Bounded file-effect metadata produced by the command broker."""

	model_config = ConfigDict(extra="ignore")

	created: list[str] = Field(default_factory=list)
	modified: list[str] = Field(default_factory=list)
	deleted: list[str] = Field(default_factory=list)
	total_created: int = 0
	total_modified: int = 0
	total_deleted: int = 0
	truncated: bool = False
	integrity_mismatches: list[str] = Field(default_factory=list)


class TraceCommand(BaseModel):
	"""Normalized metadata for one aeshell invocation.

	Raw stdout and stderr are deliberately not represented by this model.
	"""

	model_config = ConfigDict(extra="ignore")

	command_id: str
	argv: list[str]
	cwd: str
	exit_code: int | None = None
	signal: int | None = None
	complete: bool = True
	duration_ms: int | None = None
	parent_command_id: str | None = None
	file_effects: FileEffects = Field(default_factory=FileEffects)

	@field_validator("argv")
	@classmethod
	def require_argv(cls, value: list[str]) -> list[str]:
		if not value:
			raise ValueError("argv must not be empty")
		return value


class DeterministicFinding(BaseModel):
	rule_id: str
	severity: str
	command_id: str | None = None
	reason: str


class JudgeVerdict(BaseModel):
	model_config = ConfigDict(extra="forbid")

	suspicious: bool
	confidence: float = Field(ge=0.0, le=1.0)
	flagged_commands: list[str]
	reasoning: str = Field(min_length=1, max_length=4000)


class AuditReport(BaseModel):
	schema_version: str = "1"
	status: AuditStatus
	suspicious: bool
	confidence: float = Field(ge=0.0, le=1.0)
	flagged_commands: list[str] = Field(default_factory=list)
	reasoning: str
	deterministic_findings: list[DeterministicFinding] = Field(default_factory=list)
	judge_model: str | None = None
	escalated: bool = False
	trace_command_count: int = 0
	judge_command_count: int = 0
	metadata: dict[str, Any] = Field(default_factory=dict)
