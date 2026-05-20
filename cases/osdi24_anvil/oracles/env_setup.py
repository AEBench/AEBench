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
	anvil_root = context.workspace_dir
	acto_root = context.workspace_dir / "acto"

	return (
		FilesystemPathCheck(
			name="workspace_root_exists",
			path=context.workspace_dir,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="repo_osdi24_anvil",
			path=anvil_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="repo_osdi24_acto_dependency",
			path=acto_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="repo_osdi24_anvil_cargo_toml",
			path=anvil_root / "Cargo.toml",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="repo_osdi24_acto_makefile",
			path=acto_root / "Makefile",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="ref_table3",
			path=context.case_dir / "refs" / "anvil-table-3.ref.json",
			path_type=PathType.FILE,
		),
		DependencyVersionCheck(
			name="python3_version",
			cmd=("python3", "--version"),
			min_version=(3, 10, 0),
		),
	)
