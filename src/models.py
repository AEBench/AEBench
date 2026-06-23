from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from constants import (
	DEFAULT_MODEL,
	DEFAULT_PROMPT_PROFILE,
	DEFAULT_TIMEOUT_MS,
	INFRA_LOG_BASENAME,
	LOG_BASENAME_TEMPLATE,
	PROGRESS_LOG_BASENAME,
	RENDERED_LOG_BASENAME,
	RUNNER_LOG_BASENAME,
	TRANSCRIPT_BASENAME,
)
from project_config import ArtifactMode
from utils import safe_name


class _Model(BaseModel):
	model_config = ConfigDict(extra="forbid")


AgentOptionValue = str | int | bool | None | list[str] | dict[str, str]


class SourceType(str, Enum):
	LOCAL = "local"
	GIT = "git"
	ARCHIVE = "archive"
	OVERLAY = "overlay"


class RuntimeMode(str, Enum):
	LOCAL = "local"
	DOCKER = "docker"
	INHERIT = "inherit"


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


class LocalSource(_Model):
	type: Literal[SourceType.LOCAL] = SourceType.LOCAL
	path: str
	subdir: str | None = None


class GitSource(_Model):
	type: Literal[SourceType.GIT] = SourceType.GIT
	url: str
	ref: str | None = None
	subdir: str | None = None


class ArchiveSource(_Model):
	type: Literal[SourceType.ARCHIVE] = SourceType.ARCHIVE
	path: str | None = None
	url: str | None = None
	subdir: str | None = None

	@model_validator(mode="after")
	def _validate_location(self) -> "ArchiveSource":
		if bool(self.path) == bool(self.url):
			raise ValueError("archive source requires exactly one of path or url")
		return self


class OverlaySource(_Model):
	type: Literal[SourceType.OVERLAY] = SourceType.OVERLAY
	base: LocalSource | GitSource | ArchiveSource
	overlay: LocalSource


BenchSource = Annotated[
	LocalSource | GitSource | ArchiveSource | OverlaySource,
	Field(discriminator="type"),
]


class RuntimeConfig(_Model):
	mode: RuntimeMode
	image: str | None = None
	timeout_ms: int = DEFAULT_TIMEOUT_MS
	gpu: bool = False
	interactive: bool = False
	commit_before_oracle: bool = True
	keep_committed_snapshot: bool = False
	snapshot_timeout_seconds: float = 60.0

	@model_validator(mode="after")
	def _normalize(self) -> "RuntimeConfig":
		if self.timeout_ms <= 0:
			raise ValueError("runtime.timeout_ms must be positive")
		if self.snapshot_timeout_seconds <= 0:
			raise ValueError("runtime.snapshot_timeout_seconds must be positive")
		if self.mode == RuntimeMode.LOCAL:
			self.image = None
			self.gpu = False
			self.commit_before_oracle = False
			self.keep_committed_snapshot = False
		return self


class GpuRequirement(_Model):
	count: int = 1
	vram_gb: int | None = None
	architecture: str | None = None

	@model_validator(mode="after")
	def _validate_fields(self) -> "GpuRequirement":
		if self.count <= 0:
			raise ValueError("hardware.gpu.count must be positive")
		if self.vram_gb is not None and self.vram_gb <= 0:
			raise ValueError("hardware.gpu.vram_gb must be positive")
		return self


class HardwareRequirements(_Model):
	memory_gb: int | None = None
	cpu_count: int | None = None
	cpu_features: list[str] = Field(default_factory=list)
	disk_gb: int | None = None
	gpu: GpuRequirement | None = None
	network: bool = False
	notes: str | None = None

	@model_validator(mode="after")
	def _validate_fields(self) -> "HardwareRequirements":
		if self.memory_gb is not None and self.memory_gb <= 0:
			raise ValueError("hardware.memory_gb must be positive")
		if self.cpu_count is not None and self.cpu_count <= 0:
			raise ValueError("hardware.cpu_count must be positive")
		if self.disk_gb is not None and self.disk_gb <= 0:
			raise ValueError("hardware.disk_gb must be positive")
		return self


class ArtifactRequirementsConfig(_Model):
	docker: bool = False
	compose: bool = False
	hardware: HardwareRequirements | None = None

	@model_validator(mode="after")
	def _normalize(self) -> "ArtifactRequirementsConfig":
		if self.compose:
			self.docker = True
		return self


class CasePlan(_Model):
	core_claim: str
	acceptable_evidence: str
	allowed_tolerance: str

	@model_validator(mode="after")
	def _validate_fields(self) -> "CasePlan":
		for name in ("core_claim", "acceptable_evidence", "allowed_tolerance"):
			value = getattr(self, name).strip()
			if not value:
				raise ValueError(f"case_brief.{name} must not be empty")
			setattr(self, name, value)
		return self


class InstructionsConfig(_Model):

	# TODO: This is a temporary fix for ignoring the run.instructions.required_evidence field.
	# Remove this and wire required_evidence back into the agent's prompt.
	model_config = ConfigDict(extra="ignore")

	path: str = "README.md"

	@model_validator(mode="after")
	def _validate_path(self) -> "InstructionsConfig":
		path = Path(self.path.strip())
		if not self.path.strip() or path.is_absolute() or ".." in path.parts:
			raise ValueError(
				"run.instructions.path must be a non-empty relative path inside the workspace"
			)
		self.path = path.as_posix()
		return self


class PromptConfig(_Model):
	profile: PromptProfile = Field(default=PromptProfile(DEFAULT_PROMPT_PROFILE))
	append: str | None = None

	@model_validator(mode="after")
	def _normalize(self) -> "PromptConfig":
		if self.append is not None:
			self.append = self.append.strip() or None
		return self


class TaskConfig(_Model):
	id: str
	source: BenchSource | None = None
	instructions: InstructionsConfig = Field(default_factory=InstructionsConfig)
	runtime: RuntimeConfig
	artifact_requirements: ArtifactRequirementsConfig = Field(
		default_factory=ArtifactRequirementsConfig
	)
	prompt: PromptConfig = Field(default_factory=PromptConfig)
	case_brief: CasePlan | None = None

	@model_validator(mode="before")
	@classmethod
	def _accept_legacy_run_shape(cls, values: object) -> object:
		if (
			isinstance(values, dict)
			and "instructions_path" in values
			and "instructions" not in values
		):
			values = dict(values)
			values["instructions"] = {"path": values.pop("instructions_path")}
		if isinstance(values, dict) and "prompt_profile" in values and "prompt" not in values:
			values = dict(values)
			values["prompt"] = {"profile": values.pop("prompt_profile")}
		return values

	@model_validator(mode="after")
	def _validate_id(self) -> "TaskConfig":
		self.id = self.id.strip()
		if not self.id:
			raise ValueError("run.id must not be empty")
		return self

	def require_source(self) -> BenchSource:
		if self.source is None:
			raise ValueError(f"run {self.id} is missing source information")
		return self.source


class PromptArgs(_Model):
	task_text: str
	workspace_path: str
	runtime_mode: RuntimeMode
	timeout_ms: int | None = None
	interactive: bool = False
	prompt_profile: str = "artifact-eval-v1"
	prompt_append: str | None = None
	refs_path: str | None = None
	host_workspace_path: str = ""
	container_workspace_path: str | None = None
	host_agent_controls_container_shell: bool = False
	host_shell_policy: str = ""


class PromptBundle(_Model):
	profile: PromptProfile
	system_prompt: str
	initial_prompt: str


class AgentRequest(_Model):
	model: str = DEFAULT_MODEL
	system_prompt: str
	initial_prompt: str
	combined_prompt: str = ""
	interactive: bool = False
	timeout_ms: int | None = None
	agent_type: str
	agent_options: dict[str, AgentOptionValue] = Field(default_factory=dict)
	cwd: str | None = None
	add_dirs: list[str] = Field(default_factory=list)
	use_sdk_sandbox: bool = False
	max_buffer_size: int | None = None

	@model_validator(mode="after")
	def _populate_combined_prompt(self) -> "AgentRequest":
		if not self.combined_prompt:
			self.combined_prompt = f"{self.system_prompt}\n\n{self.initial_prompt}".strip()
		return self


class AgentResult(_Model):
	model: str
	exit_code: int
	message_count: int = 0
	output: str = ""


class RuntimeInfo(_Model):
	mode: RuntimeMode
	image: str | None = None
	container_id: str | None = None
	saved_image: str | None = None
	container_stopped: bool = False


class RunResult(_Model):
	id: str
	status: TaskStatus
	started_at: datetime
	finished_at: datetime
	prepare_duration_ms: int = 0
	prepare_breakdown_ms: dict[str, int] = Field(default_factory=dict)
	duration_ms: int
	workspace_path: str
	output_dir: str
	summary_path: str
	prompt_profile: PromptProfile
	runtime: RuntimeInfo
	agent_kind: str = "unknown"
	agent: AgentResult
	error: str | None = None

	@property
	def log_path(self) -> str:
		return str(Path(self.output_dir) / LOG_BASENAME_TEMPLATE.format(safe_id=safe_name(self.id)))

	@property
	def transcript_path(self) -> str:
		return str(Path(self.output_dir) / TRANSCRIPT_BASENAME)

	@property
	def rendered_log_path(self) -> str:
		return str(Path(self.output_dir) / RENDERED_LOG_BASENAME)

	@property
	def runner_log_path(self) -> str:
		return str(Path(self.output_dir) / RUNNER_LOG_BASENAME)

	@property
	def infra_log_path(self) -> str:
		return str(Path(self.output_dir) / INFRA_LOG_BASENAME)

	@property
	def progress_log_path(self) -> str:
		return str(Path(self.output_dir) / PROGRESS_LOG_BASENAME)


class RunOptions(_Model):
	model_name: str | None = None
	interactive: bool = False
	prompt_profile: PromptProfile | None = None
	prompt_append: str | None = None
	cleanup_workspace: bool = False
	skip_incompatible: bool = False


class UpstreamSourceType(str, Enum):
	VENDORED = "vendored"
	LOCAL = "local"
	GIT = "git"
	ARCHIVE = "archive"


class OracleStatus(str, Enum):
	SUCCESS = "success"
	ERROR = "error"
	PENDING = "pending"


class OracleFailureMode(str, Enum):
	FAIL_FAST = "fail_fast"
	CONTINUE = "continue"


class CaseStatus(str, Enum):
	SUCCESS = "success"
	ERROR = "error"
	INTERRUPTED = "interrupted"
	PENDING = "pending"


class OracleRuntimeConfig(_Model):
	mode: RuntimeMode | None = None
	image: str | None = None
	app_dir: str = "/"

	@model_validator(mode="after")
	def _validate(self) -> "OracleRuntimeConfig":
		if self.mode == RuntimeMode.DOCKER and not self.image:
			raise ValueError("oracle.runtime.image is required when oracle.runtime.mode = 'docker'")
		return self


class OracleConfig(_Model):
	expected_score: int = 4
	failure_mode: OracleFailureMode = OracleFailureMode.FAIL_FAST
	placeholder: bool = False
	notes: str | None = None
	runtime: OracleRuntimeConfig = Field(default_factory=OracleRuntimeConfig)

	@model_validator(mode="before")
	@classmethod
	def _ignore_removed_fields(cls, values: object) -> object:
		if not isinstance(values, dict):
			return values
		cleaned = dict(values)
		for key in (
			"phases",
			"score_mode",
			"env_setup",
			"artifact_build",
			"benchmark_prep",
			"experiment_runs",
		):
			cleaned.pop(key, None)
		return cleaned

	@model_validator(mode="after")
	def _validate_score(self) -> "OracleConfig":
		if self.expected_score <= 0:
			raise ValueError("oracle.expected_score must be positive")
		if self.notes is not None:
			self.notes = self.notes.strip() or None
		return self


class UpstreamConfig(_Model):
	source_type: UpstreamSourceType = UpstreamSourceType.VENDORED
	path: str | None = None
	url: str | None = None
	ref: str | None = None
	requested_ref: str | None = None
	resolved_at: str | None = None
	artifact_mode: ArtifactMode | None = None
	overlay_artifact: bool = False
	notes: str | None = None


class PaperConfig(_Model):
	url: str
	sha256: str
	title: str | None = None
	version: str | None = None
	venue: str | None = None
	notes: str | None = None

	@model_validator(mode="after")
	def _validate_fields(self) -> "PaperConfig":
		self.url = self.url.strip()
		if not self.url.startswith(("http://", "https://")):
			raise ValueError("paper.url must be an absolute http(s) URL")
		self.sha256 = self.sha256.strip().lower()
		if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
			raise ValueError("paper.sha256 must be a 64-character hex SHA-256 digest")
		for name in ("title", "version", "venue", "notes"):
			value = getattr(self, name)
			if value is not None:
				setattr(self, name, value.strip() or None)
		return self


class CaseConfig(_Model):
	id: str
	case_brief: CasePlan
	run: TaskConfig
	oracle: OracleConfig = Field(default_factory=OracleConfig)
	upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
	paper: PaperConfig

	@model_validator(mode="after")
	def _validate_case(self) -> "CaseConfig":
		self.id = self.id.strip()
		if not self.id:
			raise ValueError("case.id must not be empty")
		if self.run.id != self.id:
			self.run = self.run.model_copy(update={"id": self.id})
		if self.run.case_brief is None:
			self.run.case_brief = self.case_brief
		return self


class OracleInput(BaseModel):
	model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

	case_dir: Path
	artifact_dir: Path
	workspace_dir: Path
	output_dir: Path
	runtime_result: RunResult | None = None
	runtime_executor: object | None = None
	runtime_session: object | None = None
	runtime_backend: object | None = None
	oracle_config: OracleConfig | None = None


class OraclePhaseResult(_Model):
	phase: str
	status: OracleStatus
	summary: str = ""
	error: str | None = None


class OracleResult(_Model):
	status: OracleStatus
	score: int | None = None
	summary: str = ""
	phases: list[OraclePhaseResult] = Field(default_factory=list)
	artifacts: dict[str, str] = Field(default_factory=dict)
	error: str | None = None


class CaseRunResult(BaseModel):
	model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

	status: CaseStatus
	finished_at: datetime
	case_dir: str
	artifact_dir: str
	output_dir: str
	case_brief: CasePlan
	runtime_result: RunResult
	oracle_result: OracleResult

	@computed_field  # type: ignore[prop-decorator]
	@property
	def id(self) -> str:
		return self.runtime_result.id

	@computed_field  # type: ignore[prop-decorator]
	@property
	def started_at(self) -> datetime:
		return self.runtime_result.started_at

	@computed_field  # type: ignore[prop-decorator]
	@property
	def workspace_dir(self) -> str:
		return self.runtime_result.workspace_path


# Compatibility aliases for old imports. They intentionally no longer drive oracle logic.
class OraclePhaseName(str, Enum):
	ENV_SETUP = "env_setup"
	ARTIFACT_BUILD = "artifact_build"
	BENCHMARK_PREP = "benchmark_prep"
	EXPERIMENT_RUNS = "experiment_runs"


class OracleScoreMode(str, Enum):
	PHASE_COUNT = "phase_count"
