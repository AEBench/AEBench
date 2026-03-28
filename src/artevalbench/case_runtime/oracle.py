from __future__ import annotations

import importlib
import inspect
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from ..models import RunResult
from .loader import load_case_spec
from .models import (
    CaseRunResult,
    CaseSpec,
    OracleContext,
    OracleResult,
    OracleStatus,
)
from .oracle_api import (
    DiscoveredPhase,
    execute_oracle_phases,
    get_phase_registration,
    phase_key_to_string,
)


class OracleLoadError(RuntimeError):
    pass


_oracle_import_lock = threading.Lock()


def run_oracle(
    case_dir: Path,
    *,
    runtime_result: RunResult,
    output_dir: Path,
    case: CaseSpec | None = None,
) -> OracleResult:
    case_root = case_dir.resolve()
    case_spec = case or load_case_spec(case_root)
    context = OracleContext(
        case_dir=case_root,
        artifact_dir=(case_root / "artifact").resolve(),
        workspace_dir=Path(runtime_result.workspace_path).resolve(),
        output_dir=output_dir.resolve(),
        runtime_result=runtime_result,
    )
    try:
        phases = discover_oracle_phases(case_root)
        result = execute_oracle_phases(
            context,
            phases=phases,
            failure_mode=case_spec.oracle.failure_mode,
        )
    except OracleLoadError as exc:
        result = OracleResult(
            status=OracleStatus.ERROR,
            score=0,
            summary="Oracle discovery failed.",
            error=str(exc),
        )
    _oracle_output_path(output_dir).write_text(
        json.dumps(result.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return result


def discover_oracle_phases(case_dir: Path) -> list[DiscoveredPhase]:
    oracle_root = (case_dir / "oracle").resolve(strict=False)
    if not oracle_root.is_dir():
        raise OracleLoadError(f"oracle directory is missing: {oracle_root}")
    module_names = sorted(_oracle_module_names(case_dir, oracle_root))
    discovered: dict[tuple[str, ...], DiscoveredPhase] = {}
    with _oracle_import_scope(case_dir, "oracle"):
        for module_name in module_names:
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                raise OracleLoadError(f"failed to import {module_name}: {exc}") from exc
            for _name, func in inspect.getmembers(module, inspect.isfunction):
                if func.__module__ != module.__name__:
                    continue
                registration = get_phase_registration(func)
                if registration is None:
                    continue
                if registration.key in discovered:
                    previous = discovered[registration.key]
                    raise OracleLoadError(
                        "duplicate oracle phase registration for "
                        f"{registration.name}: {previous.module_name}.{previous.qualname} and "
                        f"{module_name}.{func.__qualname__}"
                    )
                discovered[registration.key] = DiscoveredPhase(
                    key=registration.key,
                    priority=registration.priority,
                    func=func,
                    module_name=module_name,
                    qualname=func.__qualname__,
                )
    if not discovered:
        raise OracleLoadError("no decorated oracle phases were found under oracle/")
    return sorted(discovered.values(), key=lambda p: (p.priority, p.key))


def write_case_result(output_dir: Path, payload: CaseRunResult) -> None:
    (output_dir / "case_result.json").write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )


def _oracle_output_path(output_dir: Path) -> Path:
    return output_dir / "oracle_result.json"


def _custom_module_from_phases(phases: list[DiscoveredPhase]) -> ModuleType | None:
    for phase in phases:
        globals_dict = getattr(phase.func, "__globals__", None)
        if not isinstance(globals_dict, dict):
            continue
        custom_module = globals_dict.get("custom")
        if isinstance(custom_module, ModuleType):
            return custom_module
    return None


def _oracle_module_names(case_dir: Path, oracle_root: Path) -> list[str]:
    module_names: list[str] = []
    for path in oracle_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(case_dir)
        if path.name == "__init__.py":
            module_rel = relative.parent
        else:
            module_rel = relative.with_suffix("")
        parts = [part for part in module_rel.parts if part]
        if not parts:
            continue
        module_names.append(".".join(parts))
    return module_names


@contextmanager
def _oracle_import_scope(case_dir: Path, package_name: str) -> Iterator[None]:
    with _oracle_import_lock:
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
