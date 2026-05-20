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
	artifact_dir = repo_root / "artifact"
	return (
		DependencyVersionCheck(
			name="python",
			cmd=("python", "--version"),
			min_version=(3, 11, 0),
		),
		DependencyVersionCheck(
			name="pip",
			cmd=("python", "-m", "pip", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="wget",
			optional=True,
			cmd=("wget", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="unzip",
			optional=True,
			cmd=("unzip", "-v"),
			min_version=(0, 0, 0),
		),
		FilesystemPathCheck(
			name="repo_root_exists",
			path=repo_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="artifact_dir_exists",
			path=artifact_dir,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="static_notebook_exists",
			path=artifact_dir / "static.ipynb",
			path_type=PathType.FILE,
		),
		DependencyVersionCheck(
			name="gcc",
			cmd=("gcc", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="nm",
			cmd=("nm", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="cmake",
			cmd=("cmake", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="make",
			cmd=("make", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="nvidia-smi",
			optional=True,
			cmd=("nvidia-smi",),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="ptxas",
			optional=True,
			cmd=("ptxas", "--version"),
			min_version=(0, 0, 0),
		),
		DependencyVersionCheck(
			name="cuobjdump",
			optional=True,
			cmd=("cuobjdump", "--version"),
			min_version=(0, 0, 0),
		),
		FilesystemPathCheck(
			name="dynamic_notebook_exists",
			path=artifact_dir / "dynamic.ipynb",
			path_type=PathType.FILE,
		),
	)
