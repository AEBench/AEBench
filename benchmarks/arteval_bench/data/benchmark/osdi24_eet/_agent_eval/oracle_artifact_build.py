"""Artifact build oracle for EET (OSDI'24).

Validates:
 - Repository working directory exists.
 - Native 'make' build command executes successfully.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path

from evaluator.oracle_artifact_build_primitives import (
    BuildCommandRequirement,
    OracleArtifactBuildBase,
)
from evaluator.utils import EntryConfig, BaseRequirement


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildTarget:
  """Declarative description of one build command to run.
  
  Attributes:
    name: Label for logs and debug printing.
    cmd: Command to execute as an argv sequence.
    rel_workdir: Directory to run cmd in (optional, default: cwd).
    optional: Whether this check is optional or the oracle should exit when failed.
    timeout_seconds: Maximum time allowed for the command to complete.
    env_vars: Environment variables to add / override.
  """

  name: str
  cmd: Sequence[str]
  rel_workdir: Path | None = None
  optional: bool = False
  timeout_seconds: float = 60.0
  env_vars: Mapping[str, str] = field(default_factory=dict)

  def __post_init__(self) -> None:
    if not self.name:
      raise ValueError("BuildTarget.name must be non-empty")

    object.__setattr__(self, "cmd", tuple(self.cmd))

    if self.rel_workdir is not None and not isinstance(self.rel_workdir, Path):
      object.__setattr__(self, "rel_workdir", Path(self.rel_workdir))


class OracleArtifactBuild(OracleArtifactBuildBase):
  """Artifact build oracle for EET."""

  def __init__(
      self,
      *,
      config: EntryConfig,
      logger: logging.Logger,
  ) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(self) -> Sequence[BaseRequirement]:
    """Returns an ordered list of build requirements to validate."""
    repo_root = self._config.repository_paths.get(self._config.name)

    if repo_root is None:
      return (BuildCommandRequirement(
          name=
          f"config: missing repository_paths entry for {self._config.name!r}",
          optional=False,
          cwd=Path(self._config.home_dir) / "__MISSING_REPOSITORY_PATH__",
          cmd=("true",),
          timeout_seconds=1.0,
      ),)

    cpu_count = os.cpu_count() or 1
    make_jobs = max(1, cpu_count // 2)

    return (BuildCommandRequirement(
        name=f"EET: make -j{make_jobs}",
        optional=False,
        cwd=repo_root,
        cmd=("make", f"-j{make_jobs}"),
        timeout_seconds=600.0,
    ),)
