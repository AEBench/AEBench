from __future__ import annotations

from pathlib import Path

from ..models import (
    CaseSpec,
    OracleContext,
    OracleFailureMode,
    OraclePhaseName,
    OraclePhaseResult,
    OracleResult,
    OracleStatus,
)
from .constants import ARTIFACT_SUBDIR
from .loader import CaseBundleError, load_case_spec


def artifact_dir_for(case_dir: Path) -> Path:
    return (case_dir.resolve() / ARTIFACT_SUBDIR).resolve(strict=False)


def has_local_artifact(case_dir: Path) -> bool:
    artifact_dir = artifact_dir_for(case_dir)
    if not artifact_dir.exists():
        return False
    return any(not entry.name.startswith(".") for entry in artifact_dir.iterdir())


__all__ = [
    "CaseBundleError",
    "CaseSpec",
    "OracleContext",
    "OracleFailureMode",
    "OraclePhaseName",
    "OraclePhaseResult",
    "OracleResult",
    "OracleStatus",
    "artifact_dir_for",
    "has_local_artifact",
    "load_case_spec",
]
