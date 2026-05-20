from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.discovery import env_setup
from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	FilesystemPathCheck,
	PathType,
)
from evaluator.oracles.utils import Checkable
from models import OracleInput


@env_setup
def oracle_env_setup(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	bpftool_src_dir = repo_root / "depsurf" / "btf" / "bpftool" / "src"
	bpftool_bin = bpftool_src_dir / "bpftool"
	return (
		DependencyVersionCheck(
			name="uv",
			cmd=("uv", "--version"),
			min_version=(0, 6, 11),
			version_regex=r"uv\s+([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="make",
			cmd=("make", "--version"),
			min_version=(4, 3, 0),
			version_regex=r"GNU Make\s+([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="patch",
			cmd=("patch", "--version"),
			min_version=(2, 7, 6),
			version_regex=r"patch\s+([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="pkg_config",
			cmd=("pkg-config", "--version"),
			min_version=(0, 29, 2),
			version_regex=r"([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="clang",
			cmd=("clang", "--version"),
			min_version=(14, 0, 0),
			version_regex=r"clang version\s+([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="llvm",
			cmd=("llvm-config", "--version"),
			min_version=(14, 0, 0),
			version_regex=r"([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="pahole",
			cmd=("pahole", "--version"),
			min_version=(1, 24, 0),
			version_regex=r"v?([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="libelf",
			cmd=("pkg-config", "--modversion", "libelf"),
			min_version=(0, 186, 0),
			version_regex=r"([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="libcap",
			cmd=("pkg-config", "--modversion", "libcap"),
			min_version=(2, 44, 0),
			version_regex=r"([0-9.]+)",
			timeout_seconds=10.0,
		),
		FilesystemPathCheck(
			name="repo_root_exists",
			path=repo_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="bpftool_src_directory_exists",
			path=bpftool_src_dir,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="bpftool_binary_exists",
			path=bpftool_bin,
			path_type=PathType.FILE,
		),
		DependencyVersionCheck(
			name="bpftool",
			cmd=(str(bpftool_bin), "version"),
			min_version=(7, 5, 0),
			version_regex=r"bpftool\s+v([0-9.]+)",
			timeout_seconds=10.0,
		),
		DependencyVersionCheck(
			name="libbpf",
			cmd=(str(bpftool_bin), "version"),
			min_version=(1, 5, 0),
			version_regex=r"libbpf\s+v([0-9.]+)",
			timeout_seconds=10.0,
		),
	)
