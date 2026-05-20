"""Validate the structure of an AEBench case bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from constants import ARTIFACT_SUBDIR, CASE_MANIFEST_FILENAME, ORACLE_DIRNAME, REFS_DIRNAME
from evaluator.loader import CaseBundleError, load_case_spec
from evaluator.oracles.discovery import OracleLoadError, discover_oracle_classes

_PHASE_CLASS_NAMES = {
    "OracleEnvSetup",
    "OracleArtifactBuild",
    "OracleBenchmarkPrep",
    "OracleExperimentRuns",
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def validate_case_bundle(case_dir: Path) -> ValidationResult:
    root = case_dir.resolve()
    manifest_path = root / CASE_MANIFEST_FILENAME
    artifact_dir = root / ARTIFACT_SUBDIR
    refs_dir = root / REFS_DIRNAME
    oracle_dir = root / ORACLE_DIRNAME
    issues: list[ValidationIssue] = []

    _require_file(manifest_path, "missing_case_toml", issues)
    _require_dir(artifact_dir, "missing_artifact_dir", issues)
    _require_dir(refs_dir, "missing_refs_dir", issues)
    _require_dir(oracle_dir, "missing_oracles_dir", issues)

    if manifest_path.is_file():
        try:
            load_case_spec(root)
        except CaseBundleError as exc:
            issues.append(
                ValidationIssue(
                    code="invalid_case_spec",
                    path=str(manifest_path),
                    message=str(exc),
                )
            )

    if oracle_dir.is_dir():
        try:
            discovered = discover_oracle_classes(root)
        except OracleLoadError as exc:
            issues.append(
                ValidationIssue(
                    code="invalid_oracles",
                    path=str(oracle_dir),
                    message=str(exc),
                )
            )
        else:
            names = {phase.cls.__name__ for phase in discovered}
            missing = sorted(_PHASE_CLASS_NAMES - names)
            if missing:
                issues.append(
                    ValidationIssue(
                        code="missing_oracle_phase_classes",
                        path=str(oracle_dir),
                        message="missing classes: " + ", ".join(missing),
                    )
                )
            _warn_on_old_decorator_style(root, issues)

    return ValidationResult(issues=tuple(issues))


def _require_file(path: Path, code: str, issues: list[ValidationIssue]) -> None:
    if not path.is_file():
        issues.append(ValidationIssue(code=code, path=str(path), message="required file is missing"))


def _require_dir(path: Path, code: str, issues: list[ValidationIssue]) -> None:
    if not path.is_dir():
        issues.append(ValidationIssue(code=code, path=str(path), message="required directory is missing"))


_OLD_DECORATORS = ("@env_setup", "@artifact_build", "@benchmark_prep", "@experiment_runs")


def _warn_on_old_decorator_style(case_dir: Path, issues: list[ValidationIssue]) -> None:
    for py_file in (case_dir / ORACLE_DIRNAME).glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        if any(marker in py_file.read_text(encoding="utf-8") for marker in _OLD_DECORATORS):
            issues.append(
                ValidationIssue(
                    code="old_decorator_oracle_style",
                    path=str(py_file),
                    message="decorator-style oracle phases are no longer supported",
                    severity="warning",
                )
            )
