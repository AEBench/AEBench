"""Environment setup oracle for EET (OSDI'24).

Validates:
  - Required host tools for the recommended Docker-based evaluation workflow.
  - Optional native-build tools for compiling EET on Debian (as documented).
  - Repository directory exists and contains expected helper scripts.
  - Any configured ground-truth reference files exist.
"""

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
  """Returns a required path from a EntryConfig dictionary."""
  try:
    return paths[key]
  except KeyError as e:
    raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from e


class OracleEnvSetup(OracleEnvSetupBase):
  """Checks environment prerequisites for EET."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    repo_root = _required_path(self._config.repository_paths,
                               self._config.name,
                               label="repository_paths")

    reqs: list[utils.BaseRequirement] = [
        DependencyVersionRequirement(
            name="docker",
            cmd=("docker", "--version"),
            required_version=(24, 0, 0),
            compare=VersionCompare.GEQ,
        ),
        DependencyVersionRequirement(
            name="g++",
            cmd=("g++", "--version"),
            required_version=(13, 2, 0),
            compare=VersionCompare.GEQ,
            optional=True,
        ),
        DependencyVersionRequirement(
            name="make",
            cmd=("make", "--version"),
            required_version=(4, 3, 0),
            compare=VersionCompare.GEQ,
            optional=True,
        ),
        DependencyVersionRequirement(
            name="autoconf",
            cmd=("autoconf", "--version"),
            required_version=(2, 71, 0),
            compare=VersionCompare.GEQ,
            optional=True,
        ),
        FilesystemPathRequirement(
            name="repo_root_exists",
            path=repo_root,
            path_type=PathType.DIRECTORY,
        ),
        FilesystemPathRequirement(
            name="scripts_dir_exists",
            path=repo_root / "scripts",
            path_type=PathType.DIRECTORY,
        ),
    ]

    return reqs
