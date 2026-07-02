"""Discovery and isolated loading of case-specific oracle implementations."""

from __future__ import annotations

import importlib
import inspect
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from constants import ORACLE_DIRNAME

from .bases import (
	CaseOracleArtifactBuildBase,
	CaseOracleBenchmarkPrepBase,
	CaseOracleEnvSetupBase,
	CaseOracleExperimentRunsBase,
	_OraclePhaseBase,
)


class OracleLoadError(RuntimeError):
	"""Raised when case oracle modules cannot be loaded or validated."""


PhaseKey = tuple[str, ...]
ENV_SETUP: PhaseKey = ("env_setup",)
ARTIFACT_BUILD: PhaseKey = ("artifact_build",)
BENCHMARK_PREP: PhaseKey = ("benchmark_prep",)
EXPERIMENT_RUNS: PhaseKey = ("experiment_runs",)

_PHASE_PRIORITIES: dict[PhaseKey, int] = {
	ENV_SETUP: 100,
	ARTIFACT_BUILD: 200,
	BENCHMARK_PREP: 300,
	EXPERIMENT_RUNS: 400,
}

# Each oracle class is classified by its framework base class
_ORACLE_BASES: tuple[tuple[type[_OraclePhaseBase], PhaseKey], ...] = (
	(CaseOracleEnvSetupBase, ENV_SETUP),
	(CaseOracleArtifactBuildBase, ARTIFACT_BUILD),
	(CaseOracleBenchmarkPrepBase, BENCHMARK_PREP),
	(CaseOracleExperimentRunsBase, EXPERIMENT_RUNS),
)


@dataclass(frozen=True, slots=True)
class DiscoveredOracleClass:
	"""Metadata for one discovered oracle phase implementation.

	Attributes:
		key: Stable identifier for the oracle phase.
		priority: Execution order for the phase.
		cls: Concrete phase implementation.
	"""

	key: PhaseKey
	priority: int
	cls: type[_OraclePhaseBase]

	@property
	def name(self) -> str:
		"""Returns the dotted phase name used in diagnostics."""
		return ".".join(self.key)


# Import discovery mutates process-global module and search-path state
_IMPORT_LOCK = threading.Lock()


def discover_oracle_classes(
	case_dir: Path,
) -> list[DiscoveredOracleClass]:
	"""Discovers and validates all oracle phases for a case.

	Args:
		case_dir: Root directory of the case.

	Returns:
		Concrete oracle phase classes in execution order.

	Raises:
		OracleLoadError: If the oracle package is missing, invalid, or
			incomplete.
	"""
	case_root = case_dir.resolve()
	oracle_root = oracle_root_for(case_root)

	with oracle_import_scope(case_root, oracle_root.name):
		return discover_oracle_classes_in_scope(case_root, oracle_root)


def discover_oracle_classes_in_scope(
	case_root: Path,
	oracle_root: Path,
) -> list[DiscoveredOracleClass]:
	"""Imports and classifies oracle modules within an active import scope.

	Exactly one concrete implementation is required for each standard phase.

	Args:
		case_root: Case directory currently available on <sys.path>.
		oracle_root: Root directory containing the oracle package.

	Returns:
		Discovered phase classes in execution order.

	Raises:
		OracleLoadError: If modules cannot be imported or phase definitions
			are missing or duplicated.
	"""
	module_names = sorted(_oracle_module_names(case_root, oracle_root))
	if not module_names:
		raise OracleLoadError(f"no Python modules found under {oracle_root}")

	discovered: dict[PhaseKey, DiscoveredOracleClass] = {}

	for module_name in module_names:
		try:
			module = importlib.import_module(module_name)
		except Exception as exc:
			raise OracleLoadError(
				f"failed to import oracle module {module_name}: {type(exc).__name__}: {exc}"
			) from exc

		for _, cls in inspect.getmembers(module, inspect.isclass):
			# Ignore imported classes and abstract support classes
			if cls.__module__ != module.__name__ or inspect.isabstract(cls):
				continue

			key = _match_phase_base(cls)
			if key is None:
				continue

			if key in discovered:
				previous = discovered[key]
				raise OracleLoadError(
					"duplicate oracle implementation for phase "
					f"{'.'.join(key)}: "
					f"{previous.cls.__qualname__} and "
					f"{cls.__qualname__}"
				)

			discovered[key] = DiscoveredOracleClass(
				key=key,
				priority=_PHASE_PRIORITIES[key],
				cls=cls,
			)

	# Missing/incomplete oracle is treated as an explicit error
	missing = [key for _, key in _ORACLE_BASES if key not in discovered]
	if missing:
		missing_names = ", ".join(".".join(key) for key in missing)
		raise OracleLoadError(
			"oracle is missing required phases: "
			f"{missing_names}; all four standard phases must be implemented"
		)

	return sorted(
		discovered.values(),
		key=lambda item: item.priority,
	)


def oracle_root_for(case_dir: Path) -> Path:
	"""Returns the required oracle package directory for a case.

	Args:
		case_dir: Root directory of the case.

	Returns:
		The resolved oracle directory.

	Raises:
		OracleLoadError: If the case has no oracle directory.
	"""
	candidate = (case_dir.resolve() / ORACLE_DIRNAME).resolve(strict=False)
	if candidate.is_dir():
		return candidate

	raise OracleLoadError(
		f"oracle directory is missing under {case_dir} (expected {ORACLE_DIRNAME}/)"
	)


def _match_phase_base(cls: type) -> PhaseKey | None:
	"""Returns the phase implemented by a concrete oracle class."""
	for base, key in _ORACLE_BASES:
		if issubclass(cls, base) and cls is not base:
			return key
	return None


def _oracle_module_names(
	case_dir: Path,
	oracle_root: Path,
) -> list[str]:
	"""Returns importable module names under an oracle package."""
	names: list[str] = []

	for path in oracle_root.rglob("*.py"):
		# Ignore generated bytecode directories or hidden directories
		if "__pycache__" in path.parts:
			continue
		if any(part.startswith(".") for part in path.relative_to(oracle_root).parts):
			continue

		relative = path.relative_to(case_dir)
		if path.name == "__init__.py":
			module_path = relative.parent
		else:
			module_path = relative.with_suffix("")

		parts = [part for part in module_path.parts if part]
		if parts:
			names.append(".".join(parts))

	return names


@contextmanager
def oracle_import_scope(
	case_dir: Path,
	package_name: str,
) -> Iterator[None]:
	"""Temporarily isolates imports for one case oracle package.

	The import lock protects the process-global <sys.path> and
	<sys.modules> mutations. Existing modules with the same package name are
	restored after discovery so separate cases cannot reuse one another's
	oracle implementations.

	Args:
		case_dir: Directory temporarily prepended to the import search path.
		package_name: Top-level oracle package name.

	Yields:
		Control while the case-specific package is importable.
	"""
	with _IMPORT_LOCK:
		module_names = [
			name
			for name in sys.modules
			if (name == package_name or name.startswith(f"{package_name}."))
		]
		saved = {name: sys.modules[name] for name in module_names}

		# Remove cached modules so imports resolve against the current case
		for name in module_names:
			del sys.modules[name]

		case_path = str(case_dir)
		sys.path.insert(0, case_path)

		try:
			yield
		finally:
			# Remove modules loaded before restoring the interpreter start state
			for name in list(sys.modules):
				if name == package_name or name.startswith(f"{package_name}."):
					del sys.modules[name]

			sys.modules.update(saved)

			try:
				sys.path.remove(case_path)
			except ValueError:
				# Best-effort cleanup if something else modified sys.path
				pass
