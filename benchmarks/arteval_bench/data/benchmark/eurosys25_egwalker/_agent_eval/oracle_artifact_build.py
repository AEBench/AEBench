"""Artifact build oracle for EGWALKER (EuroSys'25).

Validates:
  - Repository working directory exists.
  - Build commands execute successfully (captures stdout/stderr/return code).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path

from evaluator.oracle_artifact_build_primitives import (
    BuildCommandRequirement,
    OracleArtifactBuildBase,
)
from evaluator.utils import EntryConfig, BaseRequirement


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildTarget:
    """Declarative description of one build command to run.

    Kept intentionally thin: the base primitive (BuildCommandRequirement) performs
    the authoritative validation and normalization.
    """

    name: str
    cmd: Sequence[str]
    relative_workdir: Path | None = None
    optional: bool = False
    timeout_seconds: float = 60.0
    env_overrides: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("BuildTarget.name must be non-empty")

        object.__setattr__(self, "cmd", tuple(self.cmd))

        if self.relative_workdir is not None and not isinstance(
            self.relative_workdir, Path
        ):
            object.__setattr__(self, "relative_workdir", Path(self.relative_workdir))


class OracleArtifactBuild(OracleArtifactBuildBase):
    """The artifact build oracle for artifact-core.

    Defaults:
      * Runs build commands in the repo keyed by config.name.
      * EntryConfig.repository_paths is expected to contain an entry for config.name.
    """

    _DEFAULT_TARGET_SPECS: tuple[tuple[str, tuple[str, ...], float], ...] = (
        (
            "artifact-core: make tools",
            (
                "make",
                "-j8",
                "tools/diamond-types/target/release/dt",
                "tools/crdt-converter/target/release/crdt-converter",
                "tools/diamond-types/target/release/paper-stats",
                "tools/paper-benchmarks/target/memusage/paper-benchmarks",
                "tools/paper-benchmarks/target/release/paper-benchmarks",
                "tools/ot-bench/target/memusage/ot-bench",
                "tools/ot-bench/target/release/ot-bench",
            ),
            300.0,
        ),
    )

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
        return tuple(
            BuildTarget(name=name, cmd=cmd, timeout_seconds=timeout_seconds)
            for (name, cmd, timeout_seconds) in self._DEFAULT_TARGET_SPECS
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
                    timeout_seconds=1.0,
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
