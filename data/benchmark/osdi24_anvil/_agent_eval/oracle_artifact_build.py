"""Artifact build oracle for ANVIL (OSDI'24).

Validates:
  - Required repository working directories exist
  - Build commands execute successfully
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from evaluator import utils
from evaluator.oracle_artifact_build_primitives import (
    BuildCommandRequirement,
    OracleArtifactBuildBase,
)
from evaluator.utils import EntryConfig


class OracleArtifactBuild(OracleArtifactBuildBase):
  """Artifact build oracle for ANVIL."""

  def __init__(
      self,
      *,
      config: EntryConfig,
      logger: logging.Logger,
      targets: Sequence[BuildCommandRequirement] | None = None,
  ) -> None:
    super().__init__(logger=logger)
    self._config = config

    self._requirements = tuple(
        targets) if targets is not None else self._default_requirements()

    names = [r.name for r in self._requirements]
    if len(names) != len(set(names)):
      raise ValueError(f"Duplicate build requirement names: {names!r}")

  def _default_requirements(self) -> tuple[BuildCommandRequirement, ...]:
    acto_repo = self._config.repository_paths["osdi24-acto-dependency"]
    return (BuildCommandRequirement(
        name="acto: make lib",
        cwd=acto_repo,
        command=("make", "lib"),
        timeout_seconds=60.0,
    ),)

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    return self._requirements
