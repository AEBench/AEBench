"""Evaluator package public API."""

from __future__ import annotations

from pathlib import Path

from models import (
	CaseConfig,
	OracleFailureMode,
	OracleInput,
	OraclePhaseName,
	OraclePhaseResult,
	OracleResult,
	OracleScoreMode,
	OracleStatus,
)

from constants import ARTIFACT_DIRNAME
from .loader import CaseBundleError, load_case_spec


def artifact_dir_for(case_dir: Path) -> Path:
	return (case_dir.expanduser().resolve() / ARTIFACT_DIRNAME).resolve(strict=False)


def has_local_artifact(case_dir: Path) -> bool:
	artifact_dir = artifact_dir_for(case_dir)
	return artifact_dir.is_dir() and any(
		not entry.name.startswith(".") for entry in artifact_dir.iterdir()
	)


__all__ = [
	"CaseBundleError",
	"CaseConfig",
	"OracleFailureMode",
	"OracleInput",
	"OraclePhaseName",
	"OraclePhaseResult",
	"OracleResult",
	"OracleScoreMode",
	"OracleStatus",
	"artifact_dir_for",
	"has_local_artifact",
	"load_case_spec",
]
