from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.discovery import env_setup
from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	EnvironmentVariableCheck,
	EnvMatchMode,
	FilesystemPathCheck,
	PathType,
)
from evaluator.oracles.utils import Checkable
from models import OracleInput


@env_setup
def oracle_env_setup(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	venv_dir = context.workspace_dir / ".venv"
	go_root = context.workspace_dir / "go"
	go_bin = go_root / "bin"
	refs_dir = context.case_dir / "refs"

	return (
		DependencyVersionCheck(
			name="docker",
			cmd=("docker", "--version"),
			min_version=(23, 0, 0),
		),
		DependencyVersionCheck(
			name="pip3",
			cmd=("pip3", "--version"),
			min_version=(23, 0, 1),
		),
		DependencyVersionCheck(
			name="python3",
			cmd=("python3", "--version"),
			min_version=(3, 8, 0),
			version_regex=r"Python\s+([0-9.]+)",
		),
		DependencyVersionCheck(
			name="go",
			cmd=("go", "version"),
			min_version=(1, 20, 0),
			version_regex=r"go(\d+\.\d+(?:\.\d+)?)",
		),
		DependencyVersionCheck(
			name="kind",
			cmd=("kind", "version"),
			min_version=(0, 20, 0),
			version_regex=r"v([0-9.]+)",
		),
		DependencyVersionCheck(
			name="kubectl",
			cmd=("kubectl", "version", "--client", "--short"),
			min_version=(1, 22, 9),
			version_regex=r"Client Version:\s+v?([0-9.]+)",
		),
		FilesystemPathCheck(
			name="repo_root_exists",
			path=repo_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="venv_exists",
			path=venv_dir,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="go_root_exists",
			path=go_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="go_bin_exists",
			path=go_bin,
			path_type=PathType.DIRECTORY,
		),
		EnvironmentVariableCheck(
			name="PATH_contains_go_root",
			env_var="PATH",
			expected=str(go_root),
			match_mode=EnvMatchMode.CONTAINS,
		),
		EnvironmentVariableCheck(
			name="PATH_contains_go_bin",
			env_var="PATH",
			expected=str(go_bin),
			match_mode=EnvMatchMode.CONTAINS,
		),
		FilesystemPathCheck(
			name="ground_truth_table5",
			path=refs_dir / "table5.ref.json",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="ground_truth_table6",
			path=refs_dir / "table6.ref.json",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="ground_truth_table7",
			path=refs_dir / "table7.ref.json",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="ground_truth_table8",
			path=refs_dir / "table8.ref.json",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="results_table5",
			optional=True,
			path=repo_root / "table5.txt",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="results_table6",
			optional=True,
			path=repo_root / "table6.txt",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="results_table7",
			optional=True,
			path=repo_root / "table7.txt",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="results_table8",
			optional=True,
			path=repo_root / "table8.txt",
			path_type=PathType.FILE,
		),
	)
