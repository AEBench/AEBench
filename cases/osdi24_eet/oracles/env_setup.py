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
	return (
		DependencyVersionCheck(
			name="docker",
			cmd=("docker", "--version"),
			min_version=(24, 0, 0),
		),
		DependencyVersionCheck(
			name="gpp",
			cmd=("g++", "--version"),
			min_version=(13, 2, 0),
			optional=True,
		),
		DependencyVersionCheck(
			name="make",
			cmd=("make", "--version"),
			min_version=(4, 3, 0),
			optional=True,
		),
		DependencyVersionCheck(
			name="autoconf",
			cmd=("autoconf", "--version"),
			min_version=(2, 71, 0),
			optional=True,
		),
		FilesystemPathCheck(
			name="repo_root_exists",
			path=repo_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="scripts_dir_exists",
			path=repo_root / "scripts",
			path_type=PathType.DIRECTORY,
		),
	)
