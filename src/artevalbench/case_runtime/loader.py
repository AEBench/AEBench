from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from ..domain.models import CaseSpec

_ORACLE_DIRNAME = "oracle"
_REFS_DIRNAME = "refs"


class CaseBundleError(ValueError):
    pass


def load_case_spec(case_dir: Path) -> CaseSpec:
    case_root = case_dir.resolve()
    case = _read_case_spec(case_root)
    _validate_case_paths(case_root, case)
    _validate_evaluator_bundle(case_root)
    return case


def _read_case_spec(case_dir: Path) -> CaseSpec:
    toml_path = case_dir / "case.toml"
    if not toml_path.is_file():
        raise CaseBundleError(f"missing case.toml in {case_dir}")
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
    artifact_root = (case_dir / "artifact").resolve(strict=False)
    instructions = (artifact_root / case.run.instructions.path).resolve(strict=False)
    try:
        instructions.relative_to(artifact_root)
    except ValueError:
        raise CaseBundleError("instructions.path must stay within artifact/")


def _validate_evaluator_bundle(case_dir: Path) -> None:
    if not (case_dir / _REFS_DIRNAME).is_dir():
        raise CaseBundleError(f"missing {_REFS_DIRNAME}/ in {case_dir}")
    oracle_dir = case_dir / _ORACLE_DIRNAME
    if not oracle_dir.is_dir():
        raise CaseBundleError(f"missing {_ORACLE_DIRNAME}/ in {case_dir}")
    if not _has_visible_python_files(oracle_dir):
        raise CaseBundleError(f"oracle/ must contain at least one Python file")


def _has_visible_python_files(root: Path) -> bool:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        return True
    return False
