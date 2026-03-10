"""Environment setup oracle for ANVIL (OSDI'24).

Validates:
  - Required workspace and repository directories exist
  - Required reference (ground-truth) files exist
  - Required external tooling is available and satisfies minimum version constraints
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import logging

from evaluator.utils import EntryConfig
from evaluator.oracle_env_setup_primitives import (
    DependencyVersionRequirement,
    FilesystemPathRequirement,
    OracleEnvSetupBase,
    PathType,
    VersionCompare,
)


def _required_path(paths: Mapping[str, Path], key: str, *, label: str) -> Path:
  """Fetches a required path from an EntryConfig mapping with a clear error."""
  try:
    return paths[key]
  except KeyError as exc:
    raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from exc


class OracleEnvSetup(OracleEnvSetupBase):
  """Validates that the ANVIL workspace and dependencies are present."""

  _ORACLE_NAME = "EnvironmentSetup"

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(
      self
  ) -> Sequence[FilesystemPathRequirement | DependencyVersionRequirement]:
    cfg = self._config

    if not cfg.repository_paths:
      raise ValueError("EntryConfig.repository_paths must be non-empty")
    if not cfg.ground_truth_paths:
      raise ValueError("EntryConfig.ground_truth_paths must be non-empty")

    anvil_repo = _required_path(cfg.repository_paths,
                                "osdi24-anvil",
                                label="repository_paths")
    acto_repo = _required_path(cfg.repository_paths,
                               "osdi24-acto-dependency",
                               label="repository_paths")

    table3_ref = _required_path(cfg.ground_truth_paths,
                                "table3",
                                label="ground_truth_paths")

    return (
        # Workspace and repository directory layout
        FilesystemPathRequirement(
            name="home_dir",
            path=cfg.home_dir,
            path_type=PathType.DIRECTORY,
        ),
        FilesystemPathRequirement(
            name="repo_osdi24_anvil",
            path=anvil_repo,
            path_type=PathType.DIRECTORY,
        ),
        FilesystemPathRequirement(
            name="repo_osdi24_acto_dependency",
            path=acto_repo,
            path_type=PathType.DIRECTORY,
        ),

        # Reference artifacts used for evaluation
        FilesystemPathRequirement(
            name="ref_table3",
            path=table3_ref,
            path_type=PathType.FILE,
        ),

        # Tooling dependencies
        DependencyVersionRequirement(
            name="python3_version",
            cmd=("python3", "--version"),
            required_version=(3, 10, 0),
            compare=VersionCompare.GEQ,
        ),
    )
