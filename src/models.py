from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from constants import (
    DEFAULT_DOCKER_IMAGE,
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
from settings import LogLevel
from utils import safe_name


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

    @model_validator(mode="after")
    def _normalize(self) -> "RuntimeConfig":
        if self.timeout_ms <= 0:
            raise ValueError("runtime.timeout_ms must be positive")
        if self.mode == RuntimeMode.LOCAL:
            self.image = None
            self.gpu = False
        return self


class ArtifactRequirementsConfig(_Model):
    docker: bool = False
    compose: bool = False

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


class TaskConfig(_Model):
    id: str
    source: BenchSource | None = None
    instructions_path: str = "README.md"
    runtime: RuntimeConfig
    artifact_requirements: ArtifactRequirementsConfig = Field(
        default_factory=ArtifactRequirementsConfig
    )
    prompt_profile: PromptProfile = Field(
        default=PromptProfile(DEFAULT_PROMPT_PROFILE)
    )
    prompt_append: str | None = None
    case_brief: CasePlan | None = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_shape(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)

        instructions = data.pop("instructions", None)
        if isinstance(instructions, dict) and "instructions_path" not in data:
            path = instructions.get("path")
            if path is not None:
                data["instructions_path"] = path

        prompt = data.pop("prompt", None)
        if isinstance(prompt, dict):
            if "prompt_profile" not in data and prompt.get("profile") is not None:
                data["prompt_profile"] = prompt["profile"]
            if "prompt_append" not in data and prompt.get("append") is not None:
                data["prompt_append"] = prompt["append"]

        case_card = data.pop("case_card", None)
        if case_card is not None and "case_brief" not in data:
            data["case_brief"] = case_card

        return data

    @model_validator(mode="after")
    def _validate_id(self) -> "TaskConfig":
        normalized = self.id.strip()
        if not normalized:
            raise ValueError("run id must not be empty")
        self.id = normalized

        path = Path(self.instructions_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("instructions_path must stay within the workspace")
        self.instructions_path = path.as_posix()
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
    agent_options: dict[str, Any] = Field(default_factory=dict)
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


class OracleConfig(_Model):
    expected_score: int | None = None
    phases: list[str] = Field(default_factory=list)
    score_mode: OracleScoreMode | None = None
    failure_mode: OracleFailureMode = OracleFailureMode.FAIL_FAST
    placeholder: bool = False
    notes: str | None = None

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


class CaseConfig(_Model):
    id: str
    case_brief: CasePlan
    run: TaskConfig
    oracle: OracleConfig = Field(default_factory=OracleConfig)
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_shape(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        case_card = data.pop("case_card", None)
        if case_card is not None and "case_brief" not in data:
            data["case_brief"] = case_card
        return data

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

    @property
    def case_card(self) -> CasePlan:
        return self.case_brief


class OracleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    case_dir: Path
    artifact_dir: Path
    workspace_dir: Path
    output_dir: Path
    runtime_result: RunResult | None = None


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

    @computed_field  # type: ignore[misc]
    @property
    def id(self) -> str:
        return self.runtime_result.id

    @computed_field  # type: ignore[misc]
    @property
    def started_at(self) -> datetime:
        return self.runtime_result.started_at

    @computed_field  # type: ignore[misc]
    @property
    def workspace_dir(self) -> str:
        return self.runtime_result.workspace_path

    @property
    def case_card(self) -> CasePlan:
        return self.case_brief