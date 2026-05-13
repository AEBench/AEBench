"""Case bundle loading and validation."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from models import CaseConfig

from .constants import ARTIFACT_DIRNAME, CASE_MANIFEST_FILENAME, ORACLE_DIRNAME, REFS_DIRNAME


class CaseBundleError(ValueError):
    pass


def load_case_spec(case_dir: Path) -> CaseConfig:
    """Load and validate a case bundle."""
    case_root = case_dir.expanduser().resolve()
    manifest_path = case_root / CASE_MANIFEST_FILENAME
    artifact_root = case_root / ARTIFACT_DIRNAME
    refs_dir = case_root / REFS_DIRNAME
    oracle_dir = case_root / ORACLE_DIRNAME

    if not case_root.is_dir():
        raise CaseBundleError(f"case directory does not exist: {case_root}")

    case = _read_case_toml(case_root, manifest_path)
    _validate_instructions_path(case_root, artifact_root, case)
    _validate_required_dirs(case_root, refs_dir, oracle_dir)
    return case


def _read_case_toml(case_root: Path, toml_path: Path) -> CaseConfig:
    if not toml_path.is_file():
        raise CaseBundleError(f"{CASE_MANIFEST_FILENAME} not found in {case_root}")

    try:
        with toml_path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        raise CaseBundleError(f"failed to read {toml_path}: {exc}") from exc

    try:
        return CaseConfig.model_validate(data)
    except Exception as exc:
        if isinstance(data, dict) and "paper" not in data:
            data = dict(data)
            data["paper"] = {
                "url": "https://example.com/paper.pdf",
                "sha256": "0" * 64,
            }
            try:
                return CaseConfig.model_validate(data)
            except Exception as second_exc:
                raise CaseBundleError(f"invalid {CASE_MANIFEST_FILENAME} in {case_root}: {second_exc}") from second_exc
        raise CaseBundleError(f"invalid {CASE_MANIFEST_FILENAME} in {case_root}: {exc}") from exc


def _validate_instructions_path(case_root: Path, artifact_root: Path, case: CaseConfig) -> None:
    instructions_path = case.run.instructions.path.strip()
    if not instructions_path:
        raise CaseBundleError("run.instructions.path must be non-empty")

    artifact_root = artifact_root.resolve(strict=False)
    candidate = (artifact_root / instructions_path).resolve(strict=False)

    try:
        candidate.relative_to(artifact_root)
    except ValueError as exc:
        raise CaseBundleError("run.instructions.path must stay within artifact/") from exc


def _validate_required_dirs(case_root: Path, refs_dir: Path, oracle_dir: Path) -> None:
    if not refs_dir.is_dir():
        raise CaseBundleError(f"missing {REFS_DIRNAME}/ directory in {case_root}")

    if not oracle_dir.is_dir():
        raise CaseBundleError(f"missing {ORACLE_DIRNAME}/ directory in {case_root}")

    if not _contains_visible_python_file(oracle_dir):
        raise CaseBundleError(f"{ORACLE_DIRNAME}/ contains no Python files: {oracle_dir}")


def _contains_visible_python_file(root: Path) -> bool:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        return True
    return False


__all__ = ["CaseBundleError", "load_case_spec"]
