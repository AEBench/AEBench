"""Case bundle validation tests for upstream and local artifacts."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator.authoring.validate import validate_case_bundle
from evaluator.oracles.discovery import discover_oracle_classes
from evaluator.oracles.reporting import BaseCheck

WASABI_CASE = Path(__file__).resolve().parents[2] / "cases" / "sosp24_wasabi"


def test_git_upstream_case_does_not_need_materialized_artifact(tmp_path: Path) -> None:
	case_dir = tmp_path / "case"
	shutil.copytree(
		WASABI_CASE, case_dir, ignore=shutil.ignore_patterns("artifact", "__pycache__")
	)
	assert not (case_dir / "artifact").exists()
	assert validate_case_bundle(case_dir).ok


def test_wasabi_oracle_phases_construct_current_checks(tmp_path: Path) -> None:
	context = SimpleNamespace(workspace_dir=tmp_path, case_dir=WASABI_CASE)
	for phase in discover_oracle_classes(WASABI_CASE):
		oracle = object.__new__(phase.cls)
		oracle._context = context
		checks = oracle.requirements()
		assert checks
		assert all(isinstance(check, BaseCheck) for check in checks)
		if phase.name == "experiment_runs":
			assert "ground_truth_coverage_by_bucket" in {check.name for check in checks}


@pytest.mark.parametrize(
	("mode", "overlay_artifact", "required"),
	[("local", False, True), ("overlay", True, True), ("overlay", False, False)],
)
def test_artifact_directory_required_only_when_used(
	tmp_path: Path, mode: str, overlay_artifact: bool, required: bool
) -> None:
	case_dir = tmp_path / "case"
	shutil.copytree(
		WASABI_CASE, case_dir, ignore=shutil.ignore_patterns("artifact", "__pycache__")
	)
	manifest = case_dir / "case.toml"
	content = manifest.read_text(encoding="utf-8")
	content = content.replace('artifact_mode = "upstream"', f'artifact_mode = "{mode}"')
	content += f"\noverlay_artifact = {str(overlay_artifact).lower()}\n"
	manifest.write_text(content, encoding="utf-8")

	result = validate_case_bundle(case_dir)
	assert ("missing_artifact_dir" in {issue.code for issue in result.issues}) == required
