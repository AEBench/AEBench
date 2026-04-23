from __future__ import annotations

import importlib
import inspect
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..constants import ORACLE_DIRNAME
from .bases import (
    CaseOracleArtifactBuildBase,
    CaseOracleBenchmarkPrepBase,
    CaseOracleEnvSetupBase,
    CaseOracleExperimentRunsBase,
    _OraclePhaseBase,
)


class OracleLoadError(RuntimeError):
    pass


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

_ORACLE_BASES: tuple[tuple[type[_OraclePhaseBase], PhaseKey], ...] = (
    (CaseOracleEnvSetupBase, ENV_SETUP),
    (CaseOracleArtifactBuildBase, ARTIFACT_BUILD),
    (CaseOracleBenchmarkPrepBase, BENCHMARK_PREP),
    (CaseOracleExperimentRunsBase, EXPERIMENT_RUNS),
)


@dataclass(frozen=True, slots=True)
class DiscoveredOracleClass:
    key: PhaseKey
    priority: int
    cls: type[_OraclePhaseBase]

    @property
    def name(self) -> str:
        return ".".join(self.key)


_IMPORT_LOCK = threading.Lock()


def discover_oracle_classes(case_dir: Path) -> list[DiscoveredOracleClass]:
    case_root = case_dir.resolve()
    oracle_root = oracle_root_for(case_root)
    with oracle_import_scope(case_root, oracle_root.name):
        return discover_oracle_classes_in_scope(case_root, oracle_root)


def discover_oracle_classes_in_scope(case_root: Path, oracle_root: Path) -> list[DiscoveredOracleClass]:
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
            if cls.__module__ != module.__name__ or inspect.isabstract(cls):
                continue
            match = _match_phase_base(cls)
            if match is None:
                continue
            key = match
            if key in discovered:
                previous = discovered[key]
                raise OracleLoadError(
                    f"duplicate oracle implementation for phase {'.'.join(key)}: "
                    f"{previous.cls.__qualname__} and {cls.__qualname__}"
                )
            discovered[key] = DiscoveredOracleClass(key=key, priority=_PHASE_PRIORITIES[key], cls=cls)

    missing = [key for _, key in _ORACLE_BASES if key not in discovered]
    if missing:
        missing_names = ", ".join(".".join(key) for key in missing)
        raise OracleLoadError(
            f"oracle is missing required phases: {missing_names}; all four standard phases must be implemented"
        )
    return sorted(discovered.values(), key=lambda item: item.priority)


def oracle_root_for(case_dir: Path) -> Path:
    candidate = (case_dir.resolve() / ORACLE_DIRNAME).resolve(strict=False)
    if candidate.is_dir():
        return candidate
    raise OracleLoadError(f"oracle directory is missing under {case_dir} (expected {ORACLE_DIRNAME}/)")


def _match_phase_base(cls: type) -> PhaseKey | None:
    for base, key in _ORACLE_BASES:
        if issubclass(cls, base) and cls is not base:
            return key
    return None


def _oracle_module_names(case_dir: Path, oracle_root: Path) -> list[str]:
    names: list[str] = []
    for path in oracle_root.rglob("*.py"):
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
def oracle_import_scope(case_dir: Path, package_name: str) -> Iterator[None]:
    with _IMPORT_LOCK:
        module_names = [name for name in sys.modules if name == package_name or name.startswith(f"{package_name}.")]
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
