"""Benchmark preparation oracle for EGWALKER (EuroSys'25).

Validates:
  - Dataset manifest JSON is readable and well-formed.
  - Each referenced dataset file is within the repo root (no traversal).
  - Each referenced dataset file exists and matches the expected size in bytes.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Mapping, Sequence, Any

from evaluator import utils
from evaluator.utils import EntryConfig
from evaluator.oracle_benchmark_prep_primitives import (
    BenchmarkRequirement,
    FailRequirement,
    OracleBenchmarkPrepBase,
)


def _is_within(root: Path, candidate: Path) -> bool:
  """Returns True iff candidate is within root after (non-strict) resolution.

  Uses resolution to collapse '..' and resolve symlinks in existing parents
  (as much as possible without requiring the final path to exist).
  """
  root_resolved = root.resolve(strict=False)
  cand_resolved = candidate.resolve(strict=False)
  try:
    cand_resolved.relative_to(root_resolved)
    return True
  except ValueError:
    return False


class OracleBenchmarkPrep(OracleBenchmarkPrepBase):
  """Validates dataset prerequisites for _agent_eval bundles."""

  def __init__(
      self,
      *,
      config: EntryConfig,
      logger: logging.Logger,
      manifest_key: str = "datasets",
  ) -> None:
    super().__init__(logger=logger)
    self._config = config
    self._manifest_key = manifest_key

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    reqs: list[utils.BaseRequirement] = []

    repo_root = self._config.repository_paths.get(self._config.name)
    if repo_root is None:
      return [
          FailRequirement(
              name="config:repo_root",
              message=(
                  f"Missing repository_paths[{self._config.name!r}] in EntryConfig"
              ),
          )
      ]

    # Always report repo root existence as a normal requirement
    reqs.append(BenchmarkRequirement(name="repo_root_exists", filepath=repo_root))

    manifest_path = self._config.ground_truth_paths.get(self._manifest_key)
    if manifest_path is None:
      reqs.append(
          FailRequirement(
              name="config:dataset_manifest",
              message=(
                  f"Missing ground_truth_paths[{self._manifest_key!r}] in EntryConfig"
              ),
          )
      )
      return reqs

    reqs.append(
        BenchmarkRequirement(name="dataset_manifest_exists", filepath=manifest_path)
    )

    if not manifest_path.exists():
      return reqs

    try:
      obj: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
      reqs.append(
          FailRequirement(
              name="dataset_manifest_readable",
              message=f"manifest unreadable: {exc}",
          )
      )
      return reqs

    if not isinstance(obj, list):
      reqs.append(
          FailRequirement(
              name="dataset_manifest_format",
              message="manifest JSON must be a list of objects",
          )
      )
      return reqs

    # Portable size check -- prints a stable marker for signature matching
    size_script = (
        "import os, sys\n"
        "p = sys.argv[1]\n"
        "print(f'OK size = {os.path.getsize(p)}')\n"
    )

    for i, entry in enumerate(obj):
      entry_name = f"entry[{i}]"

      if not isinstance(entry, dict):
        reqs.append(FailRequirement(name=entry_name, message="entry must be an object"))
        continue

      filepath = entry.get("filepath")
      size = entry.get("sizeinbytes")

      if not isinstance(filepath, str) or not filepath.strip():
        reqs.append(FailRequirement(name=entry_name, message="missing/invalid filepath"))
        continue
      if not isinstance(size, int) or size < 0:
        reqs.append(
            FailRequirement(
                name=entry_name,
                message=f"{filepath!r}: missing/invalid sizeinbytes",
            )
        )
        continue

      rel = Path(filepath)

      # Disallow absolute paths up-front
      if rel.is_absolute():
        reqs.append(
            FailRequirement(
                name=f"dataset:{filepath}",
                message="absolute paths not allowed",
            )
        )
        continue

      full_path = repo_root / rel

      # Enforce containment (prevents '..' traversal / symlink escapes where resolvable)
      if not _is_within(repo_root, full_path):
        reqs.append(
            FailRequirement(
                name=f"dataset:{filepath}",
                message="path escapes repo root (.. traversal not allowed)",
            )
        )
        continue

      # NOTE: Existance is handled by BenchmarkRequirement(filepath=...), but 
      # size matching is handled by cmd+signature
      reqs.append(
          BenchmarkRequirement(
              name=f"dataset:{filepath}",
              filepath=full_path,
              cmd=(sys.executable, "-c", size_script, str(full_path)),
              signature=f"OK size = {size}",
              timeout_seconds=30.0,
          )
      )

    return reqs
