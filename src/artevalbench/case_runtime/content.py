from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..project_config import ProjectConfigState
from ..utils import safe_name
from .loader import CaseBundleError

_GLOB_CHARS = "*?[]"


def resolve_case_dir(
    case_input: str | Path,
    *,
    project_state: ProjectConfigState,
) -> Path:
    raw = str(case_input)
    path = Path(raw).expanduser()
    if path.exists():
        resolved = path.resolve()
        if resolved.is_dir() and (resolved / "case.toml").is_file():
            return resolved
        raise CaseBundleError(f"no case.toml found in {resolved}")

    registered = _registered_case_dir(project_state, raw)
    if registered is not None and (registered / "case.toml").is_file():
        return registered.resolve()

    bundles_root = project_state.config.resolve_bundles_dir(project_state.root)
    for candidate in _bundle_candidates(bundles_root, raw):
        if (candidate / "case.toml").is_file():
            return candidate.resolve()

    raise CaseBundleError(f"case bundle not found: {raw}")


def expand_case_dirs(
    inputs: Sequence[str | Path],
    *,
    project_state: ProjectConfigState,
) -> list[Path]:
    resolved: list[Path] = []
    for raw_input in inputs:
        raw = str(raw_input)
        path = Path(raw).expanduser()
        if path.exists():
            resolved.extend(_expand_existing_path(path))
            continue
        if _looks_like_glob(raw):
            resolved.extend(_expand_glob(path))
            continue
        resolved.append(resolve_case_dir(raw, project_state=project_state))
    return _unique_paths(resolved)


def _registered_case_dir(project_state: ProjectConfigState, case_id: str) -> Path | None:
    entry = project_state.registry.cases.get(case_id)
    if entry is None:
        return None
    return (project_state.root / entry.path).resolve()


def _expand_existing_path(path: Path) -> list[Path]:
    resolved = path.resolve()
    if resolved.is_dir() and (resolved / "case.toml").is_file():
        return [resolved]
    if resolved.is_dir():
        return [
            child.resolve()
            for child in sorted(resolved.iterdir())
            if child.is_dir() and (child / "case.toml").is_file()
        ]
    return []


def _expand_glob(path: Path) -> list[Path]:
    parent = path.parent if str(path.parent) not in {"", "."} else Path.cwd()
    return [
        match.resolve()
        for match in sorted(parent.glob(path.name))
        if match.is_dir() and (match / "case.toml").is_file()
    ]


def _bundle_candidates(bundles_root: Path, case_id: str) -> list[Path]:
    candidates = [bundles_root / case_id]
    safe_candidate = bundles_root / safe_name(case_id)
    if safe_candidate not in candidates:
        candidates.append(safe_candidate)
    return candidates


def _looks_like_glob(value: str) -> bool:
    return any(char in value for char in _GLOB_CHARS)


def _unique_paths(entries: Sequence[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for entry in entries:
        if entry in seen:
            continue
        seen.add(entry)
        unique.append(entry)
    return unique
