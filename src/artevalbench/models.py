from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..constants import (
 default_docker_image,
 default_model,
 default_prompt_profile,
 default_timeout_ms,
)
from ..project_config import ArtifactMode
from ..settings import LogLevel


class _Model(BaseModel):
	model_config = ConfigDict(extra="forbid")

	def to_json_dict(self, **kwargs) -> dict:
		return self.model_dump(mode="json", **kwargs)


class SourceType(str, Enum):
	LOCAL = "local"
	GIT = "git"
	ARCHIVE = "archive"
	OVERLAY = "overlay"


class RuntimeMode(str, Enum):
	LOCAL = "local"
	DOCKER = "docker"


class TaskStatus(str, Enum):
	SUCCESS = "success"
	ERROR = "error"
	INTERRUPTED = "interrupted"
	SKIPPED = "skipped"


class PromptProfile(str, Enum):
	ARTIFACT_EVAL_V1 = "artifact-eval-v1"
	ARTIFACT_EVAL_LOCAL_V1 = "artifact-eval-local-v1"
	ARTIFACT_EVAL_DOCKER_V1 = "artifact-eval-docker-v1"


class LiveViewMode(str, Enum):
	AUTO = "auto"
	COMPACT = "compact"
	RAW = "raw"


class LiveLayoutMode(str, Enum):
	AUTO = "auto"
	SINGLE = "single"
	SPLIT = "split"
	TRIPLE = "triple"


class UiMode(str, Enum):
	RICH = "rich"
	TEXTUAL = "textual"
	NONE = "none"


class LaunchRuntime(str, Enum):
	HOST = "host"
	CONTAINER = "container"


class LaunchTopology(str, Enum):
	PLAIN = "plain"
	MCP_COLOCATED = "mcp_colocated"
	MCP_HOST_BRIDGE = "mcp_host_bridge"


class LocalSource(BaseModel):
	model_config = ConfigDict(extra="forbid")

	type: Literal[SourceType.LOCAL] = SourceType.LOCAL
	path: str
	subdir: str | None = None


class GitSource(BaseModel):
	model_config = ConfigDict(extra="forbid")

	type: Literal[SourceType.GIT] = SourceType.GIT
	url: str
	ref: str | None = None
	subdir: str | None = None


class ArchiveSource(BaseModel):
	model_config = ConfigDict(extra="forbid")

	type: Literal[SourceType.ARCHIVE] = SourceType.ARCHIVE
	path: str | None = None
	url: str | None = None
	subdir: str | None = None

	@model_validator(mode="after")
	def _validate_location(self) -> "ArchiveSource":
		if bool(self.path) == bool(self.url):
			raise ValueError("archive source requires exactly one of 'path' or 'url'")
		return self


class OverlaySource(BaseModel):
	model_config = ConfigDict(extra="forbid")

	type: Literal[SourceType.OVERLAY] = SourceType.OVERLAY
	base: LocalSource | GitSource | ArchiveSource
	overlay: LocalSource


SourceSpec = Annotated[
 LocalSource | GitSource | ArchiveSource | OverlaySource,
 Field(discriminator="type"),
]


class InstructionSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	path: str = "README.md"

	@model_validator(mode="after")
	def _validate_relative(self) -> "InstructionSpec":
		path = Path(self.path)
		if path.is_absolute() or ".." in path.parts:
			raise ValueError("instructions.path must stay within the workspace")
		return self


class RuntimeSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	mode: RuntimeMode
	image: str | None = None
	timeout_ms: int = Field(default_factory=default_timeout_ms)
	gpu: bool = False
	interactive: bool = False

	@model_validator(mode="after")
	def _normalize(self) -> "RuntimeSpec":
		if self.mode == RuntimeMode.LOCAL:
			self.image = None
			self.gpu = False
		elif not self.image:
			self.image = default_docker_image()
		return self


class ArtifactRequirementsSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	docker: bool = False
	compose: bool = False

	@model_validator(mode="after")
	def _normalize(self) -> "ArtifactRequirementsSpec":
		if self.compose:
			self.docker = True
		return self


class PromptSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	profile: PromptProfile = Field(default_factory=lambda: PromptProfile(default_prompt_profile()))
	append: str | None = None


class CaseCardSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	core_claim: str
	acceptable_evidence: str
	allowed_tolerance: str

	@model_validator(mode="after")
	def _validate_fields(self) -> "CaseCardSpec":
		for field_name in ("core_claim", "acceptable_evidence", "allowed_tolerance"):
			value = getattr(self, field_name).strip()
			if not value:
				raise ValueError(f"case_card.{field_name} must not be empty")
			setattr(self, field_name, value)
		return self


class RunSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	id: str
	source: SourceSpec | None = None
	instructions: InstructionSpec = Field(default_factory=InstructionSpec)
	runtime: RuntimeSpec
	artifact_requirements: ArtifactRequirementsSpec = Field(
	 default_factory=ArtifactRequirementsSpec
	)
	prompt: PromptSpec = Field(default_factory=PromptSpec)
	case_card: CaseCardSpec | None = None

	@model_validator(mode="after")
	def _validate_id(self) -> "RunSpec":
		normalized = self.id.strip()
		if not normalized:
			raise ValueError("run id must not be empty")
		self.id = normalized
		return self

	def require_source(self) -> SourceSpec:
		if self.source is None:
			raise ValueError(f"run {self.id} is missing source information")
		return self.source


class PromptBundle(BaseModel):
	model_config = ConfigDict(extra="forbid")

	profile: PromptProfile
	system_prompt: str
	initial_prompt: str


class AgentRequest(BaseModel):
	model_config = ConfigDict(extra="forbid")

	model: str = Field(default_factory=default_model)
	system_prompt: str
	initial_prompt: str
	combined_prompt: str = ""
	interactive: bool = False
	timeout_ms: int | None = None
	driver_kind: str
	driver_options: dict[str, Any] = Field(default_factory=dict)
	cwd: str | None = None
	add_dirs: list[str] = Field(default_factory=list)
	use_sdk_sandbox: bool = False
	max_buffer_size: int | None = None

	@model_validator(mode="after")
	def _populate_combined_prompt(self) -> "AgentRequest":
		if not self.combined_prompt:
			if self.system_prompt:
				self.combined_prompt = f"{self.system_prompt}\n\n{self.initial_prompt}".strip()
			else:
				self.combined_prompt = self.initial_prompt
		return self


class AgentResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	model: str
	exit_code: int
	message_count: int = 0
	output: str = ""


class AgentSessionContext(BaseModel):
	model_config = ConfigDict(extra="forbid")

	task_id: str
	runtime_mode: RuntimeMode
	workspace_path: str
	host_workspace_path: str | None = None
	container_workspace_path: str | None = None
	refs_path: str | None = None
	output_dir: str
	timeout_ms: int
	prompt_profile: PromptProfile
	preferred_shell: str = "host"
	host_shell_policy: str = "primary"
	stop_state_path: str | None = None
	event_stream_path: str | None = None
	summary_path: str


class StagedPath(BaseModel):
	model_config = ConfigDict(extra="forbid")

	source: str
	target: str


class McpServerPlan(BaseModel):
	model_config = ConfigDict(extra="forbid")

	name: str
	entry_command: list[str]
	env: dict[str, str] = Field(default_factory=dict)
	resources: list[str] = Field(default_factory=list)
	tools: list[str] = Field(default_factory=list)


class AgentLaunchPlan(BaseModel):
	model_config = ConfigDict(extra="forbid")

	runtime: LaunchRuntime
	topology: LaunchTopology
	env: dict[str, str] = Field(default_factory=dict)
	staged_paths: list[StagedPath] = Field(default_factory=list)
	setup_commands: list[list[str]] = Field(default_factory=list)
	entry_command: list[str]
	result_file: str
	event_file: str | None = None
	mcp_servers: list[McpServerPlan] = Field(default_factory=list)


class AgentLaunchResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	exit_code: int
	stdout: str = ""
	stderr: str = ""
	result_file: str | None = None
	event_file: str | None = None
	runtime: LaunchRuntime
	topology: LaunchTopology


class RuntimeResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	mode: RuntimeMode
	image: str | None = None
	container_id: str | None = None
	saved_image: str | None = None
	container_stopped: bool = False


class RunResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	id: str
	status: TaskStatus
	started_at: datetime
	finished_at: datetime
	prepare_duration_ms: int = 0
	prepare_breakdown_ms: dict[str, int] = Field(default_factory=dict)
	duration_ms: int
	workspace_path: str
	log_path: str
	transcript_path: str = ""
	rendered_log_path: str = ""
	runner_log_path: str = ""
	infra_log_path: str = ""
	progress_log_path: str = ""
	summary_path: str
	prompt_profile: PromptProfile
	runtime: RuntimeResult
	agent_kind: str = "unknown"
	agent: AgentResult
	error: str | None = None


class SummaryResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	total: int
	success: int
	error: int
	interrupted: int
	skipped: int


class CliConfig(BaseModel):
	model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

	input_file: Path
	save_path: Path
	model_name: str = Field(default_factory=default_model)
	interactive: bool = False
	prompt_profile: PromptProfile | None = None
	prompt_append: str | None = None
	log_level: LogLevel | None = None


class PromptContext(BaseModel):
	model_config = ConfigDict(extra="forbid")

	task_text: str
	workspace_path: str
	runtime_mode: RuntimeMode
	timeout_ms: int
	interactive: bool
	prompt_profile: PromptProfile
	prompt_append: str | None = None
	refs_path: str | None = None
	host_workspace_path: str | None = None
	container_workspace_path: str | None = None
	preferred_shell: str = "host"
	host_shell_policy: str = "primary"
	host_agent_controls_container_shell: bool = False


class RunOptions(BaseModel):
	model_config = ConfigDict(extra="forbid")

	model_name: str = Field(default_factory=default_model)
	interactive: bool = False
	prompt_profile: PromptProfile | None = None
	prompt_append: str | None = None
	cleanup_workspace: bool = False


class UpstreamSourceType(str, Enum):
	VENDORED = "vendored"
	LOCAL = "local"
	GIT = "git"
	ARCHIVE = "archive"


class OracleStatus(str, Enum):
	SUCCESS = "success"
	ERROR = "error"
	PENDING = "pending"


class OraclePhaseName(str, Enum):
	ENV_SETUP = "env_setup"
	ARTIFACT_BUILD = "artifact_build"
	BENCHMARK_PREP = "benchmark_prep"
	EXPERIMENT_RUNS = "experiment_runs"


class OracleScoreMode(str, Enum):
	PHASE_COUNT = "phase_count"


class OracleFailureMode(str, Enum):
	FAIL_FAST = "fail_fast"
	CONTINUE = "continue"


class CaseStatus(str, Enum):
	SUCCESS = "success"
	ERROR = "error"
	INTERRUPTED = "interrupted"
	PENDING = "pending"


_PHASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class OracleSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	expected_score: int | None = None
	phases: list[str] = Field(default_factory=list)
	score_mode: OracleScoreMode | None = None
	failure_mode: OracleFailureMode = OracleFailureMode.FAIL_FAST
	placeholder: bool = False
	notes: str | None = None

	@model_validator(mode="after")
	def _validate_phases(self) -> "OracleSpec":
		normalized: list[str] = []
		seen: set[str] = set()
		for phase in self.phases:
			value = phase.strip()
			if not _PHASE_NAME_PATTERN.fullmatch(value):
				raise ValueError(
				 "oracle.phases entries must match ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"
				)
			if value in seen:
				raise ValueError("oracle.phases must not contain duplicates")
			seen.add(value)
			normalized.append(value)
		self.phases = normalized
		if self.score_mode == OracleScoreMode.PHASE_COUNT:
			if not self.phases:
				raise ValueError("oracle.phases must be non-empty when score_mode=phase_count")
			if self.expected_score is not None and self.expected_score != len(self.phases):
				raise ValueError("oracle.expected_score must equal len(oracle.phases)")
		return self


class UpstreamSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	source_type: UpstreamSourceType = UpstreamSourceType.VENDORED
	path: str | None = None
	url: str | None = None
	ref: str | None = None
	requested_ref: str | None = None
	resolved_at: str | None = None
	artifact_mode: ArtifactMode | None = None
	overlay_artifact: bool = False
	notes: str | None = None


class CaseSpec(BaseModel):
	model_config = ConfigDict(extra="forbid")

	id: str
	case_card: CaseCardSpec
	run: RunSpec
	oracle: OracleSpec = Field(default_factory=OracleSpec)
	upstream: UpstreamSpec = Field(default_factory=UpstreamSpec)

	@model_validator(mode="after")
	def _validate_case(self) -> "CaseSpec":
		normalized = self.id.strip()
		if not normalized:
			raise ValueError("case id must not be empty")
		self.id = normalized
		if self.run.id != normalized:
			self.run = self.run.model_copy(update={"id": normalized})
		return self


class OracleContext(BaseModel):
	model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

	case_dir: Path
	artifact_dir: Path
	workspace_dir: Path
	output_dir: Path
	runtime_result: RunResult


class OraclePhaseResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	phase: str
	status: OracleStatus
	summary: str = ""
	error: str | None = None


class OracleResult(BaseModel):
	model_config = ConfigDict(extra="forbid")

	status: OracleStatus
	score: int | None = None
	summary: str = ""
	phases: list[OraclePhaseResult] = Field(default_factory=list)
	artifacts: dict[str, str] = Field(default_factory=dict)
	error: str | None = None


class CaseRunResult(BaseModel):
	model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

	id: str
	status: CaseStatus
	started_at: datetime
	finished_at: datetime
	case_dir: str
	artifact_dir: str
	workspace_dir: str
	output_dir: str
	case_card: CaseCardSpec
	runtime_result: RunResult
	oracle_result: OracleResult
