"""Evaluator package public API."""

from __future__ import annotations

from pathlib import Path

from constants import ARTIFACT_DIRNAME, OraclePhaseName
from models import (
	CaseConfig,
	OracleFailureMode,
	OracleInput,
	OraclePhaseResult,
	OracleResult,
	OracleScoreMode,
	OracleStatus,
	UpstreamConfig,
)
from project_config import ArtifactMode

from .loader import CaseBundleError, load_case_spec


def artifact_dir_for(case_dir: Path) -> Path:
	return (case_dir.expanduser().resolve() / ARTIFACT_DIRNAME).resolve(strict=False)


def has_local_artifact(case_dir: Path) -> bool:
	artifact_dir = artifact_dir_for(case_dir)
	return artifact_dir.is_dir() and any(
		not entry.name.startswith(".") for entry in artifact_dir.iterdir()
	)


def local_artifact_error(case_dir: Path, upstream: UpstreamConfig) -> str | None:
	"""Return why a case cannot run from its local artifact, or None if it can."""
	if not upstream.uses_local_artifact or has_local_artifact(case_dir):
		return None
	setting = f'artifact_mode = "{upstream.artifact_mode.value}"'
	if upstream.artifact_mode == ArtifactMode.OVERLAY:
		setting += " with overlay_artifact = true"
	return f"{setting} requires a non-empty {ARTIFACT_DIRNAME}/ directory"


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
	"local_artifact_error",
]
