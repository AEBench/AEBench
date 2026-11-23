"""Workspace config tests."""

from __future__ import annotations

from pathlib import Path

from evaluator.registry import initialize_workspace
from project_config import ProjectConfig, load_project_config


def test_initialized_workspace_loads(tmp_path: Path) -> None:
	initialize_workspace(tmp_path)
	assert load_project_config(tmp_path).root == tmp_path.resolve()


def test_legacy_workspace_artifact_mode_is_ignored(tmp_path: Path) -> None:
	# Older workspaces carry artifact_mode; case.toml is the only source of truth.
	(tmp_path / "aebench.toml").write_text('artifact_mode = "local"\n', encoding="utf-8")
	state = load_project_config(tmp_path)
	assert "artifact_mode" not in ProjectConfig.model_fields
	assert "artifact_mode" not in state.config.model_dump()
