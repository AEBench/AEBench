#!/usr/bin/env python3
"""Artifact build oracle for DEPSURF (EuroSys'25).

Validates:
  - Repository working directory exists
  - The UV manager and Jupyter run succesfully
  - bpftool builds succesfully from source code stored in the repository
  - eBPF program artifacts/mini-benchmarks build succesfully
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

    object.__setattr__(self, "cmd", tuple(self.cmd))

    if self.relative_workdir is not None and not isinstance(
        self.relative_workdir, Path):
      object.__setattr__(self, "relative_workdir", Path(self.relative_workdir))


class OracleArtifactBuild(OracleArtifactBuildBase):
  """The artifact build oracle for DepSurf"""

  _DEFAULT_TARGET_SPECS: tuple[tuple[str, tuple[str, ...], float], ...] = (
      # Check the uv package manager is installed and runs (see README)
      (
          "depsurf: uv version",
          ("uv", "--version"),
          60.0,
      ),
      (
          "depsurf: uv run python import depsurf",
          ("uv", "run", "python", "-c",
           "import depsurf; print('OK depsurf import')"),
          60.0,
      ),
      (
          "depsurf: uv run python minimal",
          ("uv", "run", "python", "-c", "print('OK python')"),
          60.0,
      ),

      # Validate Jupyter is installed and runs (see README)
      (
          "depsurf: uv run jupyter lab --version (non-interactive)",
          ("uv", "run", "jupyter", "lab", "--version"),
          60.0,
      ),
      # Check bpftool builds (see 00_deps.ipynb)
      (
          "depsurf: make bpftool (in-repo)",
          ("make", "-C", "depsurf/btf/bpftool/src", "bpftool"),
          1800.0,
      ),
      (
          "depsurf: bpftool --version",
          ("bash", "-lc", "./depsurf/btf/bpftool/src/bpftool --version"),
          60.0,
      ),

      # Check eBPF program builds (see 50_programs.ipynb)
      (
          "depsurf: make bcc/libbpf-tools",
          ("bash", "-lc", "make -C data/software/bcc/libbpf-tools -j $(nproc)"),
          1800.0,
      ),
      (
          "depsurf: bcc objects exist (*.bpf.o)",
          (
              "bash",
              "-lc",
              "ls -1 data/software/bcc/libbpf-tools/.output/*.bpf.o >/dev/null",
          ),
          60.0,
      ),
      (
          "depsurf: make tracee bpf",
          ("bash", "-lc", "make -C data/software/tracee bpf -j $(nproc)"),
          1800.0,
      ),
      (
          "depsurf: tracee eBPF object exists (tracee.bpf.o)",
          ("bash", "-lc", "test -f data/software/tracee/dist/tracee.bpf.o"),
          60.0,
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
    targets: list[BuildTarget] = []
    for (name, cmd, timeout_seconds) in self._DEFAULT_TARGET_SPECS:
      optional = (name == "depsurf: uv run python minimal")
      targets.append(
          BuildTarget(
              name=name,
              cmd=cmd,
              timeout_seconds=timeout_seconds,
              optional=optional,
          ))
    return tuple(targets)

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

    return tuple(
        BuildCommandRequirement(
            name=target.name,
            optional=target.optional,
            cwd=repo_root,
            cmd=target.cmd,
            relative_workdir=target.relative_workdir,
            timeout_seconds=target.timeout_seconds,
            env_overrides=target.env_overrides,
        ) for target in self._targets)
