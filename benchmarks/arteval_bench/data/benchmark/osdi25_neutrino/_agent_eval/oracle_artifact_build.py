#!/usr/bin/env python3
"""Artifact build oracle for Neutrino (OSDI'25).

Validates:
  - Repository working directory exists.
  - The Neutrino CLI is on PATH and can invoke `--help`.
  - The Neutrino module is importable after installation.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path
import sys

from evaluator.oracle_artifact_build_primitives import (
    BuildCommandRequirement,
    OracleArtifactBuildBase,
)
from evaluator.utils import BaseRequirement, EntryConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildTarget:
    """Declarative description of one build command to run."""

    name: str
    cmd: Sequence[str]
    relative_workdir: Path | None = None
    optional: bool = False
    timeout_seconds: float = 60.0
    env_overrides: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("BuildTarget.name must be non-empty")

        if isinstance(self.cmd, (str, bytes)) or not self.cmd:
            raise ValueError("BuildTarget.cmd must be a non-empty argv sequence")

        object.__setattr__(self, "cmd", tuple(self.cmd))

        if self.relative_workdir is not None and not isinstance(self.relative_workdir, Path):
            object.__setattr__(self, "relative_workdir", Path(self.relative_workdir))


class OracleArtifactBuild(OracleArtifactBuildBase):
    """The artifact build oracle for Neutrino."""

    def __init__(
        self,
        *,
        config: EntryConfig,
        logger: logging.Logger,
        targets: Sequence[BuildTarget] | None = None,
    ) -> None:
        super().__init__(logger=logger)
        self._config = config

        if targets is None:
            targets = self._make_default_targets()
        self._targets = tuple(targets)

        names = [t.name for t in self._targets]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate build target names: {names!r}")

    def _make_default_targets(self) -> tuple[BuildTarget, ...]:
        py = sys.executable or "python"

        return (
            BuildTarget(
                name="neutrino: import test",
                cmd=(py, "-c", "import neutrino; print(neutrino.__file__)"),
                timeout_seconds=30.0,
            ),
            BuildTarget(
                name="neutrino: CLI help (optional)",
                cmd=("neutrino", "--help"),
                optional=True,
                timeout_seconds=30.0,
            ),
        )

    def requirements(self) -> Sequence[BaseRequirement]:
        """Returns an ordered list of build requirements to validate."""
        repo_root = self._config.repository_paths.get(self._config.name)

        if repo_root is None:
            return (
                BuildCommandRequirement(
                    name=f"config: missing repository_paths entry for {self._config.name!r}",
                    optional=False,
                    cwd=Path(self._config.home_dir) / "__MISSING_REPOSITORY_PATH__",
                    cmd=("true",),
                    timeout_seconds=30.0,
                ),
            )

        return tuple(
            BuildCommandRequirement(
                name=target.name,
                optional=target.optional,
                cwd=repo_root,
                cmd=target.cmd,
                relative_workdir=target.relative_workdir,
                timeout_seconds=target.timeout_seconds,
                env_overrides=target.env_overrides,
            )
            for target in self._targets
        )