"""Case bundle validation tests for upstream and local artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator.authoring.validate import validate_case_bundle
from evaluator.oracles.discovery import discover_oracle_classes
from evaluator.oracles.oracle_checks_runtime import LocalRuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck

WASABI_CASE = Path(__file__).resolve().parents[2] / "cases" / "sosp24_wasabi"


def test_git_upstream_case_does_not_need_materialized_artifact(tmp_path: Path) -> None:
	case_dir = tmp_path / "case"
	shutil.copytree(WASABI_CASE, case_dir, ignore=shutil.ignore_patterns("artifact", "__pycache__"))
	assert not (case_dir / "artifact").exists()
	assert validate_case_bundle(case_dir).ok


def test_wasabi_oracle_phases_construct_current_checks(tmp_path: Path) -> None:
	# The oracles look for benchmarks/ and results/ beside the artifact, as run.py does.
	workspace = tmp_path / "repo"
	for name in ("repo", "benchmarks", "results"):
		(tmp_path / name).mkdir()
	context = SimpleNamespace(workspace_dir=workspace, case_dir=WASABI_CASE)
	executor = LocalRuntimeCheckExecutor(default_cwd=workspace)
	for phase in discover_oracle_classes(WASABI_CASE):
		oracle = object.__new__(phase.cls)
		oracle._context = context
		oracle._default_target_name = "main"
		oracle._runtime_registry = SimpleNamespace(executor_for=lambda _name: executor)
		checks = oracle.requirements()
		assert checks
		assert all(isinstance(check, BaseCheck) for check in checks)
		if phase.name == "experiment_runs":
			assert "ground_truth_coverage" in {check.name for check in checks}


def _case_with_upstream(tmp_path: Path, **upstream: str | bool) -> Path:
	"""Copy wasabi without artifact/ and set the given upstream keys."""
	case_dir = tmp_path / "case"
	shutil.copytree(WASABI_CASE, case_dir, ignore=shutil.ignore_patterns("artifact", "__pycache__"))
	manifest = case_dir / "case.toml"
	lines = [
		line
		for line in manifest.read_text(encoding="utf-8").splitlines()
		if not line.startswith("artifact_mode")
	]
	extra = [f"{key} = {json.dumps(value)}" for key, value in upstream.items()]
	index = lines.index("[upstream]") + 1
	manifest.write_text("\n".join(lines[:index] + extra + lines[index:]) + "\n", encoding="utf-8")
	return case_dir


@pytest.mark.parametrize(
	("upstream", "required"),
	[
		({}, False),
		({"artifact_mode": "upstream"}, False),
		({"artifact_mode": "local"}, True),
		({"artifact_mode": "overlay", "overlay_artifact": True}, True),
		({"artifact_mode": "overlay", "overlay_artifact": False}, False),
	],
)
def test_artifact_directory_required_only_when_used(
	tmp_path: Path, upstream: dict[str, str | bool], required: bool
) -> None:
	result = validate_case_bundle(_case_with_upstream(tmp_path, **upstream))
	codes = {issue.code for issue in result.issues}
	assert "invalid_case_spec" not in codes
	assert ("missing_artifact_dir" in codes) == required
	assert result.ok != required
