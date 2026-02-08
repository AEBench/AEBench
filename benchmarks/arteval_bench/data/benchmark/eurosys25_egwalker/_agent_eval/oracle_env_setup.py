"""Environment setup oracle for the Eurosys'25 EGWALKER bundle.

Validates:
  - Required tools and minimum versions where applicable.
  - Repository directory exists.
  - Ground-truth reference files exist.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Sequence

from evaluator import utils
from evaluator.utils import EntryConfig
from evaluator.oracle_env_setup_primitives import (
  DependencyVersionRequirement,
  FilesystemPathRequirement,
  OracleEnvSetupBase,
  PathType,
  VersionCompare,
)


def _required_path(paths: Mapping[str, Path], key: str, *, label: str) -> Path:
  """Returns a required path from a mapping with a clear error."""
  try:
    return paths[key]
  except KeyError as e:
    raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from e


class OracleEnvSetup(OracleEnvSetupBase):
  """Validates environment prerequisites for EGWALKER."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    repo_root = _required_path(
      self._config.repository_paths, self._config.name, label="repository_paths"
    )

    reqs: list[utils.BaseRequirement] = [
      DependencyVersionRequirement(
        name = "rustc",
        cmd = ("rustc", "--version"),
        required_version = (1, 83, 0),
        compare = VersionCompare.GEQ,
      ),
      DependencyVersionRequirement(
        name = "cargo",
        cmd = ("cargo", "--version"),
        required_version = (1, 0, 0),
        compare = VersionCompare.GEQ,
      ),
      DependencyVersionRequirement(
        name = "node",
        cmd = ("node", "--version"),
        required_version = (0, 0, 0),
        compare = VersionCompare.GEQ,
      ),
      DependencyVersionRequirement(
        name = "make",
        cmd = ("make", "--version"),
        required_version = (0, 0, 0),
        compare = VersionCompare.GEQ,
        optional = True,
      ),
      FilesystemPathRequirement(
        name = "repo_root_exists",
        path = repo_root,
        path_type = PathType.DIRECTORY,
      ),
    ]

    for key, ref_path in sorted(self._config.ground_truth_paths.items()):
      reqs.append(
        FilesystemPathRequirement(
          name = f"reference_{key}_exists",
          path = ref_path,
          path_type = PathType.FILE,
        )
      )

    return reqs
