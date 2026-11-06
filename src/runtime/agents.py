"""Agent harness definitions: discovery, ``agent.toml`` validation, and credentials.

An agent is a directory holding ``agent.sh`` (starts the agent CLI) and an
optional ``agent.toml`` (what AEBench must provide to it). Adding an agent never
requires a change to AEBench itself; see docs/howtos/add_agent.md.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .backend import validate_env_var_name

if sys.version_info >= (3, 11):
	import tomllib
else:
	import tomli as tomllib

AGENT_CONFIG_FILE = "agent.toml"
AGENT_SCRIPT = "agent.sh"
INSTALL_SCRIPT = "install.sh"
AGENT_DIRS_ENV = "AEBENCH_AGENT_DIRS"
PROJECT_AGENTS_SUBDIR = "agents"
# The agents shipped with AEBench, at the root of the source checkout.
BUILTIN_AGENTS_DIR = Path(__file__).resolve().parents[2] / PROJECT_AGENTS_SUBDIR

_AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ENV_REFERENCE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Variables AEBench sets itself; a secret must not silently replace one.
_RESERVED_ENV_NAMES = frozenset({"HOME", "PATH", "PROMPT", "AGENT_CONFIG"})


class AgentConfigError(ValueError):
	"""An agent is missing, malformed, or cannot get the credentials it declares."""


class TraceFormat(str, Enum):
	"""How the trace viewer reads an agent's ``runner_output.log``."""

	CLAUDE_CODE = "claude_code"
	CODEX = "codex"
	RAW = "raw"


class _Model(BaseModel):
	model_config = ConfigDict(extra="forbid")


class AgentSecret(_Model):
	"""A credential copied from the host environment into the agent's environment."""

	# The variable the agent sees.
	name: str
	# Host variables to read, first non-empty wins. Empty means ``[name]``.
	from_env: list[str] = Field(default_factory=list)
	# A host variable naming a file that holds the value, used when no from_env variable is set.
	from_file_env: str | None = None
	hint: str | None = None

	@field_validator("name")
	@classmethod
	def _validate_name(cls, value: str) -> str:
		validate_env_var_name(value)
		if value in _RESERVED_ENV_NAMES or value.startswith("AEBENCH_"):
			raise ValueError(f"{value} is set by AEBench and cannot be a secret")
		return value

	@field_validator("from_env")
	@classmethod
	def _validate_from_env(cls, value: list[str]) -> list[str]:
		return [validate_env_var_name(name) for name in value]

	@field_validator("from_file_env")
	@classmethod
	def _validate_from_file_env(cls, value: str | None) -> str | None:
		return None if value is None else validate_env_var_name(value)


class AgentFile(_Model):
	"""A host file copied into the agent's state directory before the run."""

	# Path relative to $AEBENCH_STATE_DIR.
	target: str
	# Candidate host paths with ``~`` and ``${VAR}`` expansion. The first candidate
	# whose variables are all set is used, and it must exist.
	sources: list[str] = Field(min_length=1)
	hint: str | None = None

	@field_validator("target")
	@classmethod
	def _validate_target(cls, value: str) -> str:
		path = PurePosixPath(value)
		if not value or path.is_absolute() or ".." in path.parts:
			raise ValueError(f"file target must be a relative path inside the state dir: {value!r}")
		return value


class AgentConfig(_Model):
	description: str = ""
	# Executables that must be on PATH in the runtime; checked before the agent starts.
	requires: list[str] = Field(default_factory=list)
	# Exported as $AEBENCH_REASONING_EFFORT and recorded with the result.
	reasoning_effort: str | None = None
	# Appended to the AEBench prompt for this agent only.
	prompt_append: str | None = None
	trace_format: TraceFormat = TraceFormat.RAW
	# A script in the agent directory that converts a run directory into Claude
	# Code stream-json on stdout; used by `aebench trace export`.
	trace_converter: str | None = None
	secrets: list[AgentSecret] = Field(default_factory=list)
	files: list[AgentFile] = Field(default_factory=list)

	@field_validator("trace_converter")
	@classmethod
	def _validate_converter(cls, value: str | None) -> str | None:
		if value is None:
			return None
		path = PurePosixPath(value)
		if path.is_absolute() or ".." in path.parts:
			raise ValueError("trace_converter must be a path inside the agent directory")
		return value


@dataclass(frozen=True)
class AgentDefinition:
	name: str
	directory: Path
	config: AgentConfig

	@property
	def install_script(self) -> Path | None:
		path = self.directory / INSTALL_SCRIPT
		return path if path.is_file() else None

	@property
	def trace_converter_path(self) -> Path | None:
		converter = self.config.trace_converter
		return None if converter is None else self.directory / converter


@dataclass
class AgentCatalog:
	"""Every agent found, plus the problems that made others unusable."""

	agents: dict[str, AgentDefinition] = field(default_factory=dict)
	# Keyed by agent name, or by directory when there is no usable name.
	problems: dict[str, str] = field(default_factory=dict)
	search_dirs: list[Path] = field(default_factory=list)

	def get(self, name: str) -> AgentDefinition:
		if name in self.problems:
			raise AgentConfigError(f"agent {name!r} is unusable: {self.problems[name]}")
		agent = self.agents.get(name)
		if agent is None:
			available = ", ".join(sorted(self.agents)) or "none"
			searched = ", ".join(str(path) for path in self.search_dirs) or "none"
			raise AgentConfigError(
				f"unknown agent {name!r}; available: {available} (searched: {searched})"
			)
		return agent


def agent_search_dirs(
	project_root: Path | None = None,
	*,
	environ: Mapping[str, str] | None = None,
) -> list[Path]:
	"""Returns the directories searched for agents, most specific first.

	``$AEBENCH_AGENT_DIRS`` (os.pathsep-separated), then the project's ``agents/``,
	then the agents shipped with AEBench. Missing directories are skipped.
	"""
	env = os.environ if environ is None else environ
	candidates: list[Path] = []
	for entry in env.get(AGENT_DIRS_ENV, "").split(os.pathsep):
		if entry.strip():
			candidates.append(Path(entry.strip()).expanduser())
	if project_root is not None:
		candidates.append(project_root / PROJECT_AGENTS_SUBDIR)
	candidates.append(BUILTIN_AGENTS_DIR)

	resolved: list[Path] = []
	for candidate in candidates:
		path = candidate.resolve()
		if path.is_dir() and path not in resolved:
			resolved.append(path)
	return resolved


def discover_agents(
	project_root: Path | None = None,
	*,
	environ: Mapping[str, str] | None = None,
) -> AgentCatalog:
	catalog = AgentCatalog(search_dirs=agent_search_dirs(project_root, environ=environ))
	found: dict[str, list[Path]] = {}
	for search_dir in catalog.search_dirs:
		for directory in sorted(path for path in search_dir.iterdir() if path.is_dir()):
			has_script = (directory / AGENT_SCRIPT).is_file()
			has_config = (directory / AGENT_CONFIG_FILE).is_file()
			if not has_script and not has_config:
				continue
			name = directory.name
			found.setdefault(name, []).append(directory)
			if not _AGENT_NAME_RE.fullmatch(name):
				catalog.problems[str(directory)] = (
					"agent directory names must match [a-z0-9][a-z0-9_-]*"
				)
				continue
			try:
				catalog.agents[name] = load_agent_dir(directory)
			except AgentConfigError as exc:
				catalog.problems[name] = str(exc)

	for name, directories in found.items():
		if len(directories) > 1:
			catalog.agents.pop(name, None)
			catalog.problems[name] = "defined more than once: " + ", ".join(
				str(path) for path in directories
			)
	return catalog


def load_agent(
	name: str,
	project_root: Path | None = None,
	*,
	environ: Mapping[str, str] | None = None,
) -> AgentDefinition:
	return discover_agents(project_root, environ=environ).get(name)


def load_agent_dir(directory: Path) -> AgentDefinition:
	directory = directory.resolve()
	if not (directory / AGENT_SCRIPT).is_file():
		raise AgentConfigError(f"{directory} has no {AGENT_SCRIPT}")
	config_path = directory / AGENT_CONFIG_FILE
	data: dict[str, object] = {}
	if config_path.is_file():
		try:
			with config_path.open("rb") as handle:
				data = tomllib.load(handle)
		except tomllib.TOMLDecodeError as exc:
			raise AgentConfigError(f"{config_path}: invalid TOML: {exc}") from exc
	try:
		config = AgentConfig.model_validate(data)
	except ValidationError as exc:
		raise AgentConfigError(f"{config_path}: {_validation_summary(exc)}") from exc
	agent = AgentDefinition(name=directory.name, directory=directory, config=config)
	converter = agent.trace_converter_path
	if converter is not None and not converter.is_file():
		raise AgentConfigError(f"{config_path}: trace_converter {converter} does not exist")
	return agent


def resolve_secrets(
	agent: AgentDefinition, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
	"""Reads every declared secret from the host; raises if a required one is missing."""
	env = os.environ if environ is None else environ
	values: dict[str, str] = {}
	missing: list[str] = []
	for secret in agent.config.secrets:
		names = secret.from_env or [secret.name]
		value = next((env[name] for name in names if env.get(name)), None)
		if value is None and secret.from_file_env and env.get(secret.from_file_env):
			path = Path(env[secret.from_file_env]).expanduser()
			try:
				value = path.read_text(encoding="utf-8").strip() or None
			except OSError as exc:
				raise AgentConfigError(
					f"agent {agent.name!r}: cannot read {secret.name} from {path} "
					f"({secret.from_file_env}): {exc}"
				) from exc
		if value is not None:
			values[secret.name] = value
		else:
			missing.append(_missing_message(secret.name, secret.hint))
	if missing:
		raise AgentConfigError(f"agent {agent.name!r} is missing {'; '.join(missing)}")
	return values


def resolve_files(
	agent: AgentDefinition, *, environ: Mapping[str, str] | None = None
) -> list[tuple[Path, str]]:
	"""Returns (host source, target relative to the state dir) for every declared file."""
	env = os.environ if environ is None else environ
	resolved: list[tuple[Path, str]] = []
	missing: list[str] = []
	for entry in agent.config.files:
		source = next(
			(path for path in (_expand(candidate, env) for candidate in entry.sources) if path),
			None,
		)
		if source is not None and source.is_file():
			resolved.append((source, entry.target))
		else:
			where = str(source) if source is not None else " or ".join(entry.sources)
			missing.append(_missing_message(f"{entry.target} (looked for {where})", entry.hint))
	if missing:
		raise AgentConfigError(f"agent {agent.name!r} is missing {'; '.join(missing)}")
	return resolved


def _expand(candidate: str, env: Mapping[str, str]) -> Path | None:
	"""Expands ``${VAR}`` and ``~``; returns None when a referenced variable is unset."""
	names = _ENV_REFERENCE_RE.findall(candidate)
	if any(not env.get(name) for name in names):
		return None
	expanded = _ENV_REFERENCE_RE.sub(lambda match: env[match.group(1)], candidate)
	return Path(expanded).expanduser()


def _missing_message(what: str, hint: str | None) -> str:
	return f"{what}: {hint}" if hint else what


def _validation_summary(exc: ValidationError) -> str:
	parts = []
	for error in exc.errors():
		location = ".".join(str(item) for item in error["loc"]) or "agent.toml"
		parts.append(f"{location}: {error['msg']}")
	return "; ".join(parts)


__all__ = [
	"AgentConfigError",
	"AgentDefinition",
	"discover_agents",
	"load_agent",
	"resolve_files",
	"resolve_secrets",
]
