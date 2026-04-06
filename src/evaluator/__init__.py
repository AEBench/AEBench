"""Case-spec loading, oracle discovery, and phase execution."""

from __future__ import annotations

from pathlib import Path

from constants import ARTIFACT_SUBDIR
from models import (
    CaseConfig,
    OracleInput,
    OracleFailureMode,
    OraclePhaseName,
    OraclePhaseResult,
    OracleResult,
    OracleScoreMode,
    OracleStatus,
)

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
    "CaseConfig",
    "OracleInput",
    "OracleFailureMode",
    "OraclePhaseName",
    "OraclePhaseResult",
    "OracleResult",
    "OracleScoreMode",
    "OracleStatus",
    "artifact_dir_for",
    "has_local_artifact",
    "load_case_spec",
]
