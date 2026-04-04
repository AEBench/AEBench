from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from ..domain.models import CaseSpec
from .constants import CASE_MANIFEST_FILENAME, ORACLE_DIRNAME, REFS_DIRNAME


class CaseBundleError(ValueError):
    pass


def load_case_spec(case_dir: Path) -> CaseSpec:
    case_root = case_dir.resolve()
    case = _read_case_spec(case_root)
    _validate_case_paths(case_root, case)
    _validate_evaluator_bundle(case_root)
    return case


def _read_case_spec(case_dir: Path) -> CaseSpec:
    toml_path = case_dir / CASE_MANIFEST_FILENAME
    if not toml_path.is_file():
        raise CaseBundleError(f"case.toml not found in {case_dir}")
    try:
        with toml_path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        raise CaseBundleError(f"failed to read {toml_path}: {exc}") from exc
    try:
        return CaseSpec.model_validate(data)
    except Exception as exc:
        raise CaseBundleError(f"invalid case.toml in {case_dir}: {exc}") from exc


def _validate_case_paths(case_dir: Path, case: CaseSpec) -> None:
    artifact_dir = (case_dir / "artifact").resolve(strict=False)
    instructions_path = case.run.instructions.path.strip()
    if not instructions_path:
        raise CaseBundleError("run.instructions.path must be non-empty")
    candidate = (artifact_dir / instructions_path).resolve(strict=False)
    try:
        candidate.relative_to(artifact_dir)
    except ValueError as exc:
        raise CaseBundleError("run.instructions.path must stay within artifact/") from exc


def _validate_evaluator_bundle(case_dir: Path) -> None:
    refs_dir = case_dir / REFS_DIRNAME
    if not refs_dir.is_dir():
        raise CaseBundleError(f"missing {REFS_DIRNAME}/ in {case_dir}")
    oracle_dir = case_dir / ORACLE_DIRNAME
    if not oracle_dir.is_dir():
        raise CaseBundleError(f"missing {ORACLE_DIRNAME}/ in {case_dir}")
    if not _has_visible_python_files(oracle_dir):
        raise CaseBundleError(f"oracle directory contains no Python files: {oracle_dir}")


def _has_visible_python_files(root: Path) -> bool:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        return True
    return False


__all__ = ["CaseBundleError", "load_case_spec"]
