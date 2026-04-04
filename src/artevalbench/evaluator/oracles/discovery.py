from __future__ import annotations

import importlib
import inspect
import re
import sys
import threading

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .case_base import (
    CaseOracleArtifactBuildBase,
    CaseOracleBenchmarkPrepBase,
    CaseOracleEnvSetupBase,
    CaseOracleExperimentRunsBase,
)
from ..constants import ORACLE_DIRNAME
from .artifact_build_checks import OracleArtifactBuildBase
from .benchmark_prep_checks import OracleBenchmarkPrepBase
from .env_setup_checks import OracleEnvSetupBase
from .experiment_runs_checks import OracleExperimentRunsBase


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

ORACLE_PHASE_PRIORITIES: dict[PhaseKey, int] = {
    ENV_SETUP: 100,
    ARTIFACT_BUILD: 200,
    BENCHMARK_PREP: 300,
    EXPERIMENT_RUNS: 400,
}


@dataclass(frozen=True, slots=True)
class DiscoveredPhase:

    key: PhaseKey
    priority: int
    cls: type
    module_name: str
    qualname: str

    @property
    def name(self) -> str:
        return phase_key_to_string(self.key)


def phase_key_to_string(key: PhaseKey) -> str:
    if not key:
        raise ValueError("phase key must not be empty")
    return ".".join(key)


def phase_string_to_key(name: str) -> PhaseKey:
    phase_name = name.strip()
    if not _PHASE_NAME_PATTERN.fullmatch(phase_name):
        raise ValueError(
            "phase names must match ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"
        )
    return tuple(phase_name.split("."))


_import_lock = threading.Lock()


def discover_oracle_phases(case_dir: Path) -> list[DiscoveredPhase]:
    case_root = case_dir.resolve()
    oracle_root = _oracle_root_for(case_root)
    with oracle_import_scope(case_root, oracle_root.name):
        return discover_oracle_phases_in_scope(case_root, oracle_root)


def discover_oracle_phases_in_scope(
    case_root: Path,
    oracle_root: Path,
) -> list[DiscoveredPhase]:
    module_names = sorted(_oracle_module_names(case_root, oracle_root))
    if not module_names:
        raise OracleLoadError(f"no Python modules found under {oracle_root}")

    discovered: dict[PhaseKey, DiscoveredPhase] = {}

    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise OracleLoadError(
                f"failed to import oracle module {module_name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            base = _matching_oracle_base_class(cls)
            if base is None:
                continue

            phase_key = ORACLE_BASE_PHASE_MAP[base]
            if phase_key in discovered:
                previous = discovered[phase_key]
                raise OracleLoadError(
                    "duplicate oracle implementation for phase "
                    f"{phase_key_to_string(phase_key)}: "
                    f"{previous.module_name}.{previous.qualname} and "
                    f"{module_name}.{cls.__qualname__}"
                )

            discovered[phase_key] = DiscoveredPhase(
                key=phase_key,
                priority=ORACLE_PHASE_PRIORITIES[phase_key],
                cls=cls,
                module_name=module_name,
                qualname=cls.__qualname__,
            )

    if not discovered:
        raise OracleLoadError(
            f"no oracle base-class implementations were found under {oracle_root}"
        )

    return sorted(discovered.values(), key=lambda phase: (phase.priority, phase.key))


def oracle_root_for(case_dir: Path) -> Path:
    return _oracle_root_for(case_dir.resolve())


def _oracle_root_for(case_root: Path) -> Path:
    candidate = (case_root / ORACLE_DIRNAME).resolve(strict=False)
    if candidate.is_dir():
        return candidate
    raise OracleLoadError(
        f"oracle directory is missing under {case_root} "
        f"(expected {ORACLE_DIRNAME}/)"
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
