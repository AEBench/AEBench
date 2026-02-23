#!/usr/bin/env python3
"""Environment setup oracle for Neutrino (OSDI'25).

Validates:
  - Baseline tools for running the (static) evaluation workflow.
  - Repository directory layout and required artifact files.
  - Static and dynamic evaluation prerequisites.
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
    """Returns a required path from a mapping with a clear error."""
    try:
        return paths[key]
    except KeyError as e:
        raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from e


class OracleEnvSetup(OracleEnvSetupBase):
    """Validates environment prerequisites for Neutrino (OSDI'25)."""

    def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
        super().__init__(logger)
        self._config = config

    def requirements(self) -> Sequence[utils.BaseRequirement]:
        repo_root = _required_path(
            self._config.repository_paths, self._config.name, label="repository_paths"
        )

        artifact_dir = repo_root / "artifact"
        pkg_dir = repo_root / "neutrino"

        # Static evaluation requirements
        reqs: list[utils.BaseRequirement] = [
            DependencyVersionRequirement(
                name="python",
                cmd=("python", "--version"),
                required_version=(3, 11, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionRequirement(
                name="pip",
                cmd=("python", "-m", "pip", "--version"),
                required_version=(0, 0, 0),
                compare=VersionCompare.GEQ,
            ),
            DependencyVersionRequirement(
                name="wget",
                cmd=("wget", "--version"),
                required_version=(0, 0, 0),
                compare=VersionCompare.GEQ,
                optional=True,
            ),
            DependencyVersionRequirement(
                name="unzip",
                cmd=("unzip", "-v"),
                required_version=(0, 0, 0),
                compare=VersionCompare.GEQ,
                optional=True,
            ),
            FilesystemPathRequirement(
                name="repo_root_exists",
                path=repo_root,
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathRequirement(
                name="artifact_dir_exists",
                path=artifact_dir,
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathRequirement(
                name="static_notebook_exists",
                path=artifact_dir / "static.ipynb",
                path_type=PathType.FILE,
            ),
        ]

        # Dynamic evaluation requirements
        reqs.extend(
            [
                DependencyVersionRequirement(
                    name="gcc",
                    cmd=("gcc", "--version"),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=False,
                ),
                DependencyVersionRequirement(
                    name="nm",
                    cmd=("nm", "--version"),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=False,
                ),
                DependencyVersionRequirement(
                    name="cmake",
                    cmd=("cmake", "--version"),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=False,
                ),
                DependencyVersionRequirement(
                    name="make",
                    cmd=("make", "--version"),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=False,
                ),
                DependencyVersionRequirement(
                    name="nvidia-smi",
                    cmd=("nvidia-smi",),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=True,
                ),
                DependencyVersionRequirement(
                    name="ptxas",
                    cmd=("ptxas", "--version"),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=True,
                ),
                DependencyVersionRequirement(
                    name="cuobjdump",
                    cmd=("cuobjdump", "--version"),
                    required_version=(0, 0, 0),
                    compare=VersionCompare.GEQ,
                    optional=True,
                ),
                FilesystemPathRequirement(
                    name="dynamic_notebook_exists",
                    path=artifact_dir / "dynamic.ipynb",
                    path_type=PathType.FILE,
                ),
            ]
        )

        return reqs
