from __future__ import annotations

import importlib
import inspect
import logging
import re
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, cast

from models import OracleInput

from ..constants import ORACLE_DIRNAME
from .artifact_build_checks import OracleArtifactBuildBase
from .benchmark_prep_checks import OracleBenchmarkPrepBase
from .case_base import (
	CaseOracleArtifactBuildBase,
	CaseOracleBenchmarkPrepBase,
	CaseOracleEnvSetupBase,
	CaseOracleExperimentRunsBase,
)
from .env_setup_checks import OracleEnvSetupBase
from .experiment_runs_checks import OracleExperimentRunsBase
from .utils import Checkable

_PHASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class OracleLoadError(RuntimeError):
	pass


PhaseKey = tuple[str, ...]
ENV_SETUP: PhaseKey = ("env_setup",)
ARTIFACT_BUILD: PhaseKey = ("artifact_build",)
BENCHMARK_PREP: PhaseKey = ("benchmark_prep",)
EXPERIMENT_RUNS: PhaseKey = ("experiment_runs",)

ORACLE_BASE_PHASE_MAP: dict[type, PhaseKey] = {
	CaseOracleEnvSetupBase: ENV_SETUP,
	CaseOracleArtifactBuildBase: ARTIFACT_BUILD,
	CaseOracleBenchmarkPrepBase: BENCHMARK_PREP,
	CaseOracleExperimentRunsBase: EXPERIMENT_RUNS,
	OracleEnvSetupBase: ENV_SETUP,
	OracleArtifactBuildBase: ARTIFACT_BUILD,
	OracleBenchmarkPrepBase: BENCHMARK_PREP,
	OracleExperimentRunsBase: EXPERIMENT_RUNS,
}

_PHASE_PRIORITIES: dict[PhaseKey, int] = {
	ENV_SETUP: 100,
	ARTIFACT_BUILD: 200,
	BENCHMARK_PREP: 300,
	EXPERIMENT_RUNS: 400,
}

REQUIRED_PHASE_KEYS: frozenset[PhaseKey] = frozenset(_PHASE_PRIORITIES)


class OraclePhase(Protocol):
	"""Callable that produces checks for a phase given oracle context."""

	def __call__(self, context: OracleInput) -> Sequence[Checkable]:
		raise NotImplementedError


@dataclass(frozen=True, slots=True)
class DiscoveredPhase:
	key: PhaseKey
	priority: int
	requirements: OraclePhase
	qualname: str

	def __post_init__(self) -> None:
		if self.key not in _PHASE_PRIORITIES:
			allowed = ", ".join(phase_key_to_string(k) for k in sorted(_PHASE_PRIORITIES))
			raise ValueError(
				f"unknown oracle phase key {phase_key_to_string(self.key)!r}; "
				f"allowed phases: {allowed}"
			)

	@property
	def name(self) -> str:
		return phase_key_to_string(self.key)

	@classmethod
	def from_class(cls, oracle_cls: type, key: PhaseKey) -> DiscoveredPhase:
		"""Wrap a class-based oracle phase into a DiscoveredPhase."""

		def requirements(context: OracleInput) -> Sequence[Checkable]:
			logger = logging.getLogger(f"oracle.{phase_key_to_string(key)}")
			instance = oracle_cls(context=context, logger=logger)
			return instance.requirements()  # type: ignore[no-any-return]

		return cls(
			key=key,
			priority=_PHASE_PRIORITIES[key],
			requirements=requirements,
			qualname=oracle_cls.__qualname__,
		)


def phase_key_to_string(key: PhaseKey) -> str:
	if not key:
		raise ValueError("phase key must not be empty")
	return ".".join(key)


def phase_string_to_key(name: str) -> PhaseKey:
	phase_name = name.strip()
	if not _PHASE_NAME_PATTERN.fullmatch(phase_name):
		raise ValueError("phase names must match ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$")
	return tuple(phase_name.split("."))


def _phase_decorator(key: PhaseKey) -> Callable[[OraclePhase], DiscoveredPhase]:
	"""Create a decorator that registers a function as an oracle phase."""

	def decorator(fn: OraclePhase) -> DiscoveredPhase:
		return DiscoveredPhase(
			key=key,
			priority=_PHASE_PRIORITIES[key],
			requirements=fn,
			qualname=getattr(fn, "__qualname__", repr(fn)),
		)

	return decorator


env_setup = _phase_decorator(ENV_SETUP)
artifact_build = _phase_decorator(ARTIFACT_BUILD)
benchmark_prep = _phase_decorator(BENCHMARK_PREP)
experiment_runs = _phase_decorator(EXPERIMENT_RUNS)


_import_lock = threading.Lock()


def discover_oracle_phases(case_dir: Path) -> list[DiscoveredPhase]:
	"""Find oracle phases (class-based and decorator-based) under case_dir/oracles/."""
	case_root = case_dir.resolve()
	oracle_root = _oracle_root_for(case_root)
	with oracle_import_scope(case_root, oracle_root.name):
		return discover_oracle_phases_in_scope(case_root, oracle_root)


def discover_oracle_phases_in_scope(
	case_root: Path,
	oracle_root: Path,
) -> list[DiscoveredPhase]:
	"""Find oracle phases assuming import scope is already held."""
	module_names = sorted(_oracle_module_names(case_root, oracle_root))
	if not module_names:
		raise OracleLoadError(f"no Python modules found under {oracle_root}")

	discovered: dict[PhaseKey, DiscoveredPhase] = {}

	def _register(phase: DiscoveredPhase, source: str) -> None:
		if phase.key in discovered:
			previous = discovered[phase.key]
			raise OracleLoadError(
				"duplicate oracle implementation for phase "
				f"{phase_key_to_string(phase.key)}: "
				f"{previous.qualname} and {source}"
			)
		discovered[phase.key] = phase

	for module_name in module_names:
		try:
			module = importlib.import_module(module_name)
		except Exception as exc:
			raise OracleLoadError(
				f"failed to import oracle module {module_name}: {type(exc).__name__}: {exc}"
			) from exc

		# Class-based oracle phases
		for _name, cls in inspect.getmembers(module, inspect.isclass):
			if cls.__module__ != module.__name__:
				continue
			base = _matching_oracle_base_class(cls)
			if base is None:
				continue
			phase_key = ORACLE_BASE_PHASE_MAP[base]
			_register(
				DiscoveredPhase.from_class(cls, phase_key),
				f"{module_name}.{cls.__qualname__}",
			)

		# Decorator-based oracle phases (module-level DiscoveredPhase instances)
		for _name, obj in inspect.getmembers(module):
			if isinstance(obj, DiscoveredPhase):
				_register(obj, f"{module_name}.{obj.qualname}")

	if not discovered:
		raise OracleLoadError(f"no oracle phase implementations were found under {oracle_root}")

	return sorted(discovered.values(), key=lambda phase: (phase.priority, phase.key))


def oracle_root_for(case_dir: Path) -> Path:
	return _oracle_root_for(case_dir.resolve())


def _oracle_root_for(case_root: Path) -> Path:
	candidate = (case_root / ORACLE_DIRNAME).resolve(strict=False)
	if candidate.is_dir():
		return candidate
	raise OracleLoadError(
		f"oracle directory is missing under {case_root} (expected {ORACLE_DIRNAME}/)"
	)


def _matching_oracle_base_class(cls: type) -> type | None:
	if inspect.isabstract(cls):
		return None
	for base in ORACLE_BASE_PHASE_MAP:
		if issubclass(cls, base) and cls is not base:
			return base
	return None


def _oracle_module_names(case_dir: Path, oracle_root: Path) -> list[str]:
	module_names: list[str] = []
	for path in oracle_root.rglob("*.py"):
		if "__pycache__" in path.parts:
			continue
		if any(part.startswith(".") for part in path.relative_to(oracle_root).parts):
			continue
		relative = path.relative_to(case_dir)
		if path.name == "__init__.py":
			module_rel = relative.parent
		else:
			module_rel = relative.with_suffix("")
		parts = [part for part in module_rel.parts if part]
		if parts:
			module_names.append(".".join(parts))
	return module_names


@contextmanager
def oracle_import_scope(case_dir: Path, package_name: str) -> Iterator[None]:
	"""Add case_dir on sys.path, isolate oracle imports, and roll back before exit."""
	with _import_lock:
		module_names = [
			name
			for name in sys.modules
			if name == package_name or name.startswith(f"{package_name}.")
		]
		saved = {name: sys.modules[name] for name in module_names}

		for name in module_names:
			del sys.modules[name]

		case_path = str(case_dir)
		sys.path.insert(0, case_path)
		try:
			yield
		finally:
			for name in list(sys.modules):
				if name == package_name or name.startswith(f"{package_name}."):
					del sys.modules[name]
			sys.modules.update(saved)
			try:
				sys.path.remove(case_path)
			except ValueError:
				pass


def merge_toml_and_python_phases(
	python_phases: list[DiscoveredPhase],
	toml_phases: list[DiscoveredPhase],
) -> list[DiscoveredPhase]:
	"""Merge TOML-declared phases into Python-discovered phases.

	When both sources define checks for the same phase key, the merged phase
	chains Python checks first, then TOML checks.
	"""
	merged: dict[PhaseKey, DiscoveredPhase] = {}

	for phase in python_phases:
		merged[phase.key] = phase

	for toml_phase in toml_phases:
		if toml_phase.key in merged:
			py_phase = merged[toml_phase.key]

			def _make_chained(py_req: OraclePhase, toml_req: OraclePhase) -> OraclePhase:
				def chained(ctx: OracleInput) -> Sequence[Checkable]:
					return list(py_req(ctx)) + list(toml_req(ctx))

				return cast(OraclePhase, chained)

			merged[toml_phase.key] = DiscoveredPhase(
				key=toml_phase.key,
				priority=toml_phase.priority,
				requirements=_make_chained(py_phase.requirements, toml_phase.requirements),
				qualname=f"{py_phase.qualname}+{toml_phase.qualname}",
			)
		else:
			merged[toml_phase.key] = toml_phase

	return sorted(merged.values(), key=lambda p: (p.priority, p.key))
