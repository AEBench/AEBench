"""Benchmark preparation oracle for DEPSURF.

Validates:
  - Dataset directory layout is correct 
  - Each dataset subdirectory contains the expected raw files
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evaluator import utils
from evaluator.oracle_benchmark_prep_primitives import (
    BenchmarkRequirement,
    FailRequirement,
    OracleBenchmarkPrepBase,
)
from evaluator.utils import EntryConfig


def _is_within(root: Path, candidate: Path) -> bool:
  """Returns True iff candidate is within root after (non-strict) resolution."""
  root_resolved = root.resolve(strict=False)
  cand_resolved = candidate.resolve(strict=False)
  try:
    cand_resolved.relative_to(root_resolved)
    return True
  except ValueError:
    return False


def _required_repo_root(config: EntryConfig) -> Path | None:
  """Returns the configured repo root (or None if missing)."""
  return config.repository_paths.get(config.name)


def _format_list(items: Sequence[str], *, max_items: int = 12) -> str:
  if not items:
    return ""
  head = list(items[:max_items])
  more = len(items) - len(head)
  suffix = f"\n... ({more} more)" if more > 0 else ""
  return "\n".join(f"- {x}" for x in head) + suffix


def _as_nonempty_str(value: object, *, label: str) -> str:
  if not isinstance(value, str) or not value.strip():
    raise ValueError(f"{label} must be a non-empty string")
  return value.strip()


def _as_str_list(value: object, *, label: str) -> tuple[str, ...]:
  if not isinstance(value, (list, tuple)):
    raise ValueError(f"{label} must be a list of strings")
  out: list[str] = []
  for i, v in enumerate(value):
    if not isinstance(v, str) or not v.strip():
      raise ValueError(f"{label}[{i}] must be a non-empty string")
    out.append(v.strip())
  if not out:
    raise ValueError(f"{label} must be non-empty")
  if len(out) != len(set(out)):
    raise ValueError(f"{label} contains duplicates: {out!r}")
  return tuple(out)


@dataclasses.dataclass(frozen=True, slots=True)
class DatasetSpec:
  """Validated dataset layout specification derived from EntryConfig.metadata."""
  dataset_root: Path
  subdirs: tuple[str, ...]
  basenames: tuple[str, ...]


def _parse_dataset_spec(
    config: EntryConfig,
    *,
    repo_root: Path,
) -> DatasetSpec | tuple[utils.BaseRequirement, ...]:
  """Parses dataset metadata and returns DatasetSpec, or FailRequirements."""
  md = config.metadata or {}

  try:
    rel_raw = md.get("dataset_relpath")
    subdirs_raw = md.get("dataset_subdirs")
    basenames_raw = md.get("dataset_basenames")

    dataset_relpath = _as_nonempty_str(rel_raw,
                                       label="metadata['dataset_relpath']")
    dataset_subdirs = _as_str_list(subdirs_raw,
                                   label="metadata['dataset_subdirs']")
    dataset_basenames = _as_str_list(basenames_raw,
                                     label="metadata['dataset_basenames']")
  except ValueError as exc:
    return (FailRequirement(
        name="config:dataset_metadata",
        message=str(exc),
    ),)

  rel = Path(dataset_relpath)

  if rel.is_absolute():
    return (FailRequirement(
        name="config:dataset_relpath",
        message=f"dataset_relpath must be relative, got: {dataset_relpath!r}",
    ),)
  if ".." in rel.parts:
    return (FailRequirement(
        name="config:dataset_relpath",
        message=f"dataset_relpath must not contain '..': {dataset_relpath!r}",
    ),)

  dataset_root = (repo_root / rel)

  if not _is_within(repo_root, dataset_root):
    return (FailRequirement(
        name="config:dataset_relpath",
        message=
        f"dataset_root escapes repo_root: dataset_root={dataset_root} repo_root={repo_root}",
    ),)

  for s in dataset_subdirs:
    p = Path(s)
    if p.is_absolute() or ".." in p.parts:
      return (FailRequirement(
          name="config:dataset_subdirs",
          message=
          f"dataset_subdirs must be relative and not contain '..': bad entry: {s!r}",
      ),)

  return DatasetSpec(
      dataset_root=dataset_root,
      subdirs=dataset_subdirs,
      basenames=dataset_basenames,
  )


def _path_requirements(
    *,
    repo_root: Path,
    dataset_root: Path,
    subdirs: Sequence[str],
) -> list[utils.BaseRequirement]:
  """Returns path requirements for dataset layout (existence + type checks)."""
  reqs: list[utils.BaseRequirement] = []

  reqs.append(
      BenchmarkRequirement(name="dataset_root_exists", filepath=dataset_root))
  for s in subdirs:
    reqs.append(
        BenchmarkRequirement(
            name=f"dataset_subdir_exists:{s}",
            filepath=(dataset_root / s),
        ))

  if dataset_root.exists() and not dataset_root.is_dir():
    reqs.append(
        FailRequirement(
            name="dataset_root_is_dir",
            message=
            f"dataset_root exists but is not a directory: {dataset_root}",
        ))

  for s in subdirs:
    p = dataset_root / s
    if p.exists() and not p.is_dir():
      reqs.append(
          FailRequirement(
              name=f"dataset_subdir_is_dir:{s}",
              message=f"dataset subdir exists but is not a directory: {p}",
          ))

  return reqs


def _dirs_ready(*, dataset_root: Path, subdir_paths: Iterable[Path]) -> bool:
  """Returns True iff dataset_root and all subdirs exist and are directories."""
  if not dataset_root.exists() or not dataset_root.is_dir():
    return False
  for p in subdir_paths:
    if not p.exists() or not p.is_dir():
      return False
  return True


def _present_basenames(dir_path: Path) -> set[str]:
  """Returns basenames (file stem) for direct children files under dir_path."""
  out: set[str] = set()
  for p in dir_path.iterdir():
    # Ignore hidden files (e.g., .gitignore)
    if p.name.startswith("."):
      continue
    if not p.is_file():
      continue
    out.add(p.stem)
  return out


def _basename_requirements(
    *,
    dataset_root: Path,
    subdirs: Sequence[str],
    expected_basenames: Sequence[str],
) -> list[utils.BaseRequirement]:
  """Returns requirements ensuring expected basenames exist under each subdir."""
  reqs: list[utils.BaseRequirement] = []

  expected_set = set(expected_basenames)
  if not expected_set:
    reqs.append(
        FailRequirement(
            name="config:dataset_basenames",
            message="metadata['dataset_basenames'] must be non-empty",
        ))
    return reqs

  for s in subdirs:
    subdir_path = dataset_root / s
    present = _present_basenames(subdir_path)

    missing = sorted(expected_set - present)
    extra = sorted(present - expected_set)

    if missing:
      reqs.append(
          FailRequirement(
              name=f"dataset_basenames_missing:{s}",
              message=
              (f"dataset subdir is missing required basenames: {subdir_path}\n"
               f"{_format_list(missing)}"),
          ))

    if extra:
      reqs.append(
          FailRequirement(
              name=f"dataset_basenames_extra:{s}",
              optional=True,
              message=
              (f"dataset subdir contains unexpected basenames: {subdir_path}\n"
               f"{_format_list(extra)}"),
          ))

  return reqs


class OracleBenchmarkPrep(OracleBenchmarkPrepBase):
  """Validates dataset prerequisites for DEPSURF evaluation bundles."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    reqs: list[utils.BaseRequirement] = []

    repo_root = _required_repo_root(self._config)
    if repo_root is None:
      return [
          FailRequirement(
              name="config:repo_root",
              message=
              f"Missing repository_paths[{self._config.name!r}] in EntryConfig",
          )
      ]

    reqs.append(
        BenchmarkRequirement(name="repo_root_exists", filepath=repo_root))

    spec_or_err = _parse_dataset_spec(self._config, repo_root=repo_root)
    if not isinstance(spec_or_err, DatasetSpec):
      reqs.extend(spec_or_err)
      return reqs

    spec = spec_or_err

    reqs.extend(
        _path_requirements(
            repo_root=repo_root,
            dataset_root=spec.dataset_root,
            subdirs=spec.subdirs,
        ))

    subdir_paths = [spec.dataset_root / s for s in spec.subdirs]
    if _dirs_ready(dataset_root=spec.dataset_root, subdir_paths=subdir_paths):
      reqs.extend(
          _basename_requirements(
              dataset_root=spec.dataset_root,
              subdirs=spec.subdirs,
              expected_basenames=spec.basenames,
          ))

    return reqs
