"""Environment setup oracle for DEPSURF (EuroSys'25).

Validates:
  - Required system tools are installed and meet minimum versions
  - Expected repository paths and bpftool artifacts exist
  - bpftool/libbpf versions are compatible
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from evaluator import utils
from evaluator.oracle_env_setup_primitives import (
    DependencyVersionRequirement,
    FilesystemPathRequirement,
    OracleEnvSetupBase,
    PathType,
    VersionCompare,
)


class OracleEnvSetup(OracleEnvSetupBase):
  """DEPSURF environment setup oracle."""

  def __init__(self, *, config: utils.EntryConfig,
               logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config
    self._repo_root = Path(
        self._config.repository_paths[self._config.name]).resolve()

    # Required paths (see 00_deps.ipynb)
    self._bpftool_src_dir = self._repo_root / "depsurf" / "btf" / "bpftool" / "src"
    self._bpftool_bin = self._bpftool_src_dir / "bpftool"

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    return (
        # Core dependencies
        DependencyVersionRequirement(
            name="uv",
            cmd=("uv", "--version"),
            required_version=(0, 6, 11),
            compare=VersionCompare.GEQ,
            version_regex=r"uv\s+([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="make",
            cmd=("make", "--version"),
            required_version=(4, 3, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"GNU Make\s+([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="patch",
            cmd=("patch", "--version"),
            required_version=(2, 7, 6),
            compare=VersionCompare.GEQ,
            version_regex=r"patch\s+([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="pkg-config",
            cmd=("pkg-config", "--version"),
            required_version=(0, 29, 2),
            compare=VersionCompare.GEQ,
            version_regex=r"([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="clang",
            cmd=("clang", "--version"),
            required_version=(14, 0, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"clang version\s+([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="llvm",
            cmd=("llvm-config", "--version"),
            required_version=(14, 0, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="pahole",
            cmd=("pahole", "--version"),
            required_version=(1, 25, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"v?([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="libelf",
            cmd=("pkg-config", "--modversion", "libelf"),
            required_version=(0, 186, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="libcap",
            cmd=("pkg-config", "--modversion", "libcap"),
            required_version=(2, 44, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"([0-9.]+)",
            timeout_seconds=10.0,
        ),

        # Repository layout
        FilesystemPathRequirement(
            name="repo root directory exists",
            path=self._repo_root,
            path_type=PathType.DIRECTORY,
        ),

        # bpftool artifacts (see 00_deps.ipynb)
        FilesystemPathRequirement(
            name="bpftool src directory exists",
            path=self._bpftool_src_dir,
            path_type=PathType.DIRECTORY,
        ),
        FilesystemPathRequirement(
            name="bpftool binary exists",
            path=self._bpftool_bin,
            path_type=PathType.FILE,
        ),
        DependencyVersionRequirement(
            name="bpftool",
            cmd=(str(self._bpftool_bin), "version"),
            required_version=(7, 5, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"bpftool\s+v([0-9.]+)",
            timeout_seconds=10.0,
        ),
        DependencyVersionRequirement(
            name="libbpf",
            cmd=(str(self._bpftool_bin), "version"),
            required_version=(1, 5, 0),
            compare=VersionCompare.GEQ,
            version_regex=r"libbpf\s+v([0-9.]+)",
            timeout_seconds=10.0,
        ),
    )
