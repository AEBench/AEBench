from __future__ import annotations

import importlib.util
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..domain.models import OracleContext, OracleFailureMode, OraclePhaseResult, OracleResult, OracleStatus

_PHASE_SEGMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PHASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_SKIPPED_PHASE_SUMMARY = "skipped because a previous phase failed"
_PHASE_REGISTRATION_ATTR = "__artevalbench_phase_registration__"

PhaseKey = tuple[str, ...]
PhaseReturn = str | None
PhaseCallable = Callable[[OracleContext], PhaseReturn]
ENV_SETUP: PhaseKey = ("env_setup",)
ARTIFACT_BUILD: PhaseKey = ("artifact_build",)
BENCHMARK_PREP: PhaseKey = ("benchmark_prep",)
EXPERIMENT_RUNS: PhaseKey = ("experiment_runs",)


class OraclePhaseError(RuntimeError):
    def __init__(self, summary: str, *, error: str | None = None) -> None:
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise ValueError("phase failure summary must not be empty")
        super().__init__(normalized_summary)
        self.summary = normalized_summary
        self.error = error.strip() if error and error.strip() else None


class EnvSetupError(OraclePhaseError):
    pass


class ArtifactBuildError(OraclePhaseError):
    pass


class BenchmarkPrepError(OraclePhaseError):
    pass


class ExperimentRunsError(OraclePhaseError):
    pass


@dataclass(frozen=True)
class PhaseRegistration:
    key: PhaseKey
    priority: int = 0

    @property
    def name(self) -> str:
        return phase_key_to_string(self.key)


@dataclass(frozen=True)
class DiscoveredPhase:
    key: PhaseKey
    priority: int
    func: PhaseCallable
    module_name: str
    qualname: str

    @property
    def name(self) -> str:
        return phase_key_to_string(self.key)


def phase(
    *segments_or_tuple: str | Sequence[str],
    priority: int = 0,
) -> Callable[[PhaseCallable], PhaseCallable]:
    if not segments_or_tuple:
        raise ValueError("phase decorator requires at least one phase segment")
    if (
        len(segments_or_tuple) == 1
        and isinstance(segments_or_tuple[0], Sequence)
        and not isinstance(segments_or_tuple[0], (str, bytes, bytearray))
    ):
        raw_segments: Sequence[str] = segments_or_tuple[0]
    else:
        raw_segments = tuple(str(s) for s in segments_or_tuple)
    if not raw_segments:
        raise ValueError("phase decorator requires at least one phase segment")
    key = tuple(_validated_segment(str(s)) for s in raw_segments)

    def decorator(func: PhaseCallable) -> PhaseCallable:
        existing = get_phase_registration(func)
        if existing is not None:
            raise ValueError(
                f"phase already registered on {func.__module__}.{func.__qualname__}: {existing.name}"
            )
        setattr(func, _PHASE_REGISTRATION_ATTR, PhaseRegistration(key=key, priority=priority))
        return func

    return decorator


def get_phase_registration(func: object) -> PhaseRegistration | None:
    value = getattr(func, _PHASE_REGISTRATION_ATTR, None)
    if isinstance(value, PhaseRegistration):
        return value
    return None


def phase_key_to_string(key: PhaseKey) -> str:
    if not key:
        raise ValueError("phase key must not be empty")
    return ".".join(key)


def phase_string_to_key(name: str) -> PhaseKey:
    phase_name = name.strip()
    if not _PHASE_NAME_PATTERN.fullmatch(phase_name):
        raise ValueError("phase names must match ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$")
    return tuple(phase_name.split("."))


def execute_oracle_phases(
    context: OracleContext,
    *,
    phases: Sequence[DiscoveredPhase],
    failure_mode: OracleFailureMode | str = OracleFailureMode.FAIL_FAST,
    on_phase_start: Callable[[DiscoveredPhase], None] | None = None,
    on_phase_finish: Callable[[DiscoveredPhase, OraclePhaseResult], None] | None = None,
) -> OracleResult:
    mode = OracleFailureMode(failure_mode)
    if not phases:
        return OracleResult(
            status=OracleStatus.SUCCESS, score=0, summary="No oracle phases configured."
        )

    results: list[OraclePhaseResult] = []
    failed_phases: list[str] = []
    for index, phase_def in enumerate(phases):
        if on_phase_start is not None:
            on_phase_start(phase_def)
        try:
            outcome = phase_def.func(context)
            if outcome is None:
                summary = f"{phase_def.name} passed"
            elif isinstance(outcome, str):
                summary = outcome.strip()
                if not summary:
                    raise ValueError("phase success summaries must not be empty")
            else:
                raise TypeError("phase functions must return None or a summary string")
            phase_result = OraclePhaseResult(
                phase=phase_def.name,
                status=OracleStatus.SUCCESS,
                summary=summary,
            )
            results.append(phase_result)
            if on_phase_finish is not None:
                on_phase_finish(phase_def, phase_result)
        except OraclePhaseError as exc:
            phase_result = OraclePhaseResult(
                phase=phase_def.name,
                status=OracleStatus.ERROR,
                summary=exc.summary,
                error=exc.error or str(exc),
            )
            results.append(phase_result)
            if on_phase_finish is not None:
                on_phase_finish(phase_def, phase_result)
            failed_phases.append(phase_def.name)
            if mode == OracleFailureMode.FAIL_FAST:
                results.extend([
                    OraclePhaseResult(
                        phase=p.name,
                        status=OracleStatus.PENDING,
                        summary=_SKIPPED_PHASE_SUMMARY,
                    )
                    for p in phases[index + 1:]
                ])
                break
        except Exception as exc:
            phase_result = OraclePhaseResult(
                phase=phase_def.name,
                status=OracleStatus.ERROR,
                summary="phase raised an unexpected exception",
                error=f"{type(exc).__name__}: {exc}",
            )
            results.append(phase_result)
            if on_phase_finish is not None:
                on_phase_finish(phase_def, phase_result)
            failed_phases.append(phase_def.name)
            if mode == OracleFailureMode.FAIL_FAST:
                results.extend([
                    OraclePhaseResult(
                        phase=p.name,
                        status=OracleStatus.PENDING,
                        summary=_SKIPPED_PHASE_SUMMARY,
                    )
                    for p in phases[index + 1:]
                ])
                break

    score = sum(1 for result in results if result.status == OracleStatus.SUCCESS)
    return OracleResult(
        status=OracleStatus.SUCCESS if not failed_phases else OracleStatus.ERROR,
        score=score,
        summary=f"Passed {score}/{len(phases)} phases.",
        phases=results,
        error=None if not failed_phases else f"oracle failed phases: {', '.join(failed_phases)}",
    )


def require_path_exists(
    path: Path,
    summary: str,
    *,
    kind: str = "any",
    exc_type: type[OraclePhaseError] = OraclePhaseError,
) -> None:
    if not path.exists():
        raise exc_type(summary, error=str(path))
    if kind == "file" and not path.is_file():
        raise exc_type(summary, error=f"expected a file at {path}")
    if kind == "dir" and not path.is_dir():
        raise exc_type(summary, error=f"expected a directory at {path}")


def require_command_exists(
    command: str,
    summary: str,
    *,
    exc_type: type[OraclePhaseError] = OraclePhaseError,
) -> None:
    if shutil.which(command) is None:
        raise exc_type(summary, error=f"command not found on PATH: {command}")


def require_python_module_exists(
    module_name: str,
    summary: str,
    *,
    exc_type: type[OraclePhaseError] = OraclePhaseError,
) -> None:
    if importlib.util.find_spec(module_name) is None:
        raise exc_type(summary, error=f"python module is not importable: {module_name}")


def require_file_text_equals(
    path: Path,
    expected_text: str,
    summary: str,
    *,
    exc_type: type[OraclePhaseError] = OraclePhaseError,
) -> None:
    if not path.is_file():
        raise exc_type(summary, error=f"expected output file is missing: {path}")
    observed_text = path.read_text(encoding="utf-8").strip()
    if observed_text != expected_text:
        raise exc_type(
            summary,
            error=f"expected={expected_text!r} observed={observed_text!r}",
        )


def _validated_segment(segment: str) -> str:
    value = segment.strip()
    if not _PHASE_SEGMENT_PATTERN.fullmatch(value):
        raise ValueError("phase segments must match ^[a-z][a-z0-9_]*$")
    return value


__all__ = [
    "ARTIFACT_BUILD",
    "ArtifactBuildError",
    "BENCHMARK_PREP",
    "BenchmarkPrepError",
    "DiscoveredPhase",
    "ENV_SETUP",
    "EXPERIMENT_RUNS",
    "EnvSetupError",
    "ExperimentRunsError",
    "OraclePhaseError",
    "PhaseRegistration",
    "execute_oracle_phases",
    "get_phase_registration",
    "phase",
    "phase_key_to_string",
    "phase_string_to_key",
    "require_command_exists",
    "require_file_text_equals",
    "require_path_exists",
    "require_python_module_exists",
]
