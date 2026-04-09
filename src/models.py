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
			raise ValueError("archive source requires exactly one of 'path' or 'url'")
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
	timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS)
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
		for field_name in ("core_claim", "acceptable_evidence", "allowed_tolerance"):
			value = getattr(self, field_name).strip()
			if not value:
				raise ValueError(f"case_brief.{field_name} must not be empty")
			setattr(self, field_name, value)
		return self


class InstructionsConfig(_Model):
	path: str = "README.md"

	@model_validator(mode="after")
	def _validate_path(self) -> "InstructionsConfig":
		normalized = self.path.strip()
		if not normalized:
			raise ValueError("run.instructions.path must not be empty")
		path = Path(normalized)
		if path.is_absolute() or ".." in path.parts:
			raise ValueError("run.instructions.path must stay within the workspace")
		self.path = path.as_posix()
		return self


class PromptConfig(_Model):
	profile: PromptProfile = Field(default=PromptProfile(DEFAULT_PROMPT_PROFILE))
	append: str | None = None

	@model_validator(mode="after")
	def _normalize(self) -> "PromptConfig":
		if self.append is not None:
			normalized = self.append.strip()
			self.append = normalized or None
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

	@model_validator(mode="after")
	def _validate_id(self) -> "TaskConfig":
		normalized = self.id.strip()
		if not normalized:
			raise ValueError("run id must not be empty")
		self.id = normalized
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
	model: str = Field(default=DEFAULT_MODEL)
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
			if self.system_prompt:
				self.combined_prompt = f"{self.system_prompt}\n\n{self.initial_prompt}".strip()
			else:
				self.combined_prompt = self.initial_prompt
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
	model_name: str = Field(default=DEFAULT_MODEL)
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


class PathCheckDecl(_Model):
	type: Literal["path"] = "path"
	name: str
	path: str
	path_type: Literal["any", "file", "directory"] = "any"
	optional: bool = False


class VersionCheckDecl(_Model):
	type: Literal["version"] = "version"
	name: str
	cmd: list[str]
	required_version: list[int]
	compare: Literal["eq", "geq", "leq"] = "geq"
	version_regex: str | None = None
	timeout_seconds: float = 5.0
	optional: bool = False

	@model_validator(mode="after")
	def _validate_version(self) -> "VersionCheckDecl":
		if len(self.required_version) != 3:
			raise ValueError("required_version must have exactly 3 elements [major, minor, patch]")
		if any(v < 0 for v in self.required_version):
			raise ValueError("required_version elements must be non-negative")
		return self


class EnvVarCheckDecl(_Model):
	type: Literal["env_var"] = "env_var"
	name: str
	env_var: str
	expected: str
	match_mode: Literal["exact", "contains", "regex"] = "exact"
	optional: bool = False


class CommandCheckDecl(_Model):
	type: Literal["command"] = "command"
	name: str
	cmd: str | list[str]
	cwd: str | None = None
	signature: str | None = None
	timeout_seconds: float = 60.0
	env_overrides: dict[str, str] = Field(default_factory=dict)
	use_shell: bool = False
	optional: bool = False

	@model_validator(mode="after")
	def _validate_command(self) -> "CommandCheckDecl":
		if isinstance(self.cmd, str):
			if not self.cmd.strip():
				raise ValueError("cmd must be a non-empty string")
			if not self.use_shell:
				raise ValueError("cmd must be a list[str] when use_shell is False")
		else:
			if not self.cmd:
				raise ValueError("cmd must be a non-empty list[str]")
			if any(not part.strip() for part in self.cmd):
				raise ValueError("cmd list elements must be non-empty strings")
		return self


class ExprCheckDecl(_Model):
	type: Literal["expr"] = "expr"
	name: str
	expr: str
	observed: str | None = None
	reference: str | None = None
	optional: bool = False


CheckDecl = Annotated[
	PathCheckDecl | VersionCheckDecl | EnvVarCheckDecl | CommandCheckDecl | ExprCheckDecl,
	Field(discriminator="type"),
]


class PhaseChecksConfig(_Model):
	checks: list[CheckDecl] = Field(default_factory=list)


class OracleConfig(_Model):
	expected_score: int | None = None
	phases: list[str] = Field(default_factory=list)
	score_mode: OracleScoreMode | None = None
	failure_mode: OracleFailureMode = OracleFailureMode.FAIL_FAST
	placeholder: bool = False
	notes: str | None = None
	env_setup: PhaseChecksConfig = Field(default_factory=PhaseChecksConfig)
	artifact_build: PhaseChecksConfig = Field(default_factory=PhaseChecksConfig)
	benchmark_prep: PhaseChecksConfig = Field(default_factory=PhaseChecksConfig)
	experiment_runs: PhaseChecksConfig = Field(default_factory=PhaseChecksConfig)

	@property
	def has_toml_checks(self) -> bool:
		return bool(
			self.env_setup.checks
			or self.artifact_build.checks
			or self.benchmark_prep.checks
			or self.experiment_runs.checks
		)

	@model_validator(mode="after")
	def _validate_phases(self) -> "OracleConfig":
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
		url = self.url.strip()
		if not url:
			raise ValueError("paper.url must not be empty")
		if not (url.startswith("https://") or url.startswith("http://")):
			raise ValueError("paper.url must be an absolute http(s) URL")
		self.url = url

		sha256 = self.sha256.strip().lower()
		if not re.fullmatch(r"[0-9a-f]{64}", sha256):
			raise ValueError("paper.sha256 must be a 64-character hex SHA-256 digest")
		self.sha256 = sha256

		for field_name in ("title", "version", "venue", "notes"):
			value = getattr(self, field_name)
			if value is None:
				continue
			normalized = value.strip()
			if not normalized:
				raise ValueError(f"paper.{field_name} must not be empty when provided")
			setattr(self, field_name, normalized)
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
		normalized = self.id.strip()
		if not normalized:
			raise ValueError("case id must not be empty")
		self.id = normalized
		if self.run.id != normalized:
			self.run = self.run.model_copy(update={"id": normalized})
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
