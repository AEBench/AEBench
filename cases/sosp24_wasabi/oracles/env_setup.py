from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.discovery import env_setup
from evaluator.oracles.env_setup_checks import (
	DependencyVersionCheck,
	FilesystemPathCheck,
	PathType,
)
from evaluator.oracles.utils import Checkable, RuntimeCheckExecutor
from models import OracleInput


@env_setup
def oracle_env_setup(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	benchmarks_root = context.workspace_dir / "benchmarks"

	def _check_runtime_dir_env(var_name: str) -> utils.CheckResult:
		executor: RuntimeCheckExecutor | None = context.runtime_executor  # type: ignore[assignment]
		try:
			raw_value = utils.read_check_env_var(var_name, executor=executor)
		except (RuntimeError, ValueError) as exc:
			return utils.CheckResult.failure(str(exc))
		if raw_value is None or not raw_value.strip():
			return utils.CheckResult.failure(f"{var_name} is not set")
		proc = utils.run_check_process_capture(
			cmd=(
				"python3",
				"-c",
				(
					"import os, sys; "
					"value = os.environ.get(sys.argv[1], '').strip(); "
					"sys.exit(0 if value and os.path.isdir(value) else 1)"
				),
				var_name,
			),
			cwd=repo_root,
			env=None,
			timeout_seconds=5.0,
			executor=executor,
		)
		if proc.timed_out:
			return utils.CheckResult.failure(
				f"{var_name} directory check timed out",
				stdout=proc.stdout,
				stderr=proc.stderr,
				timed_out=True,
				cwd=repo_root,
			)
		if proc.returncode != 0:
			return utils.CheckResult.failure(
				f"{var_name} does not point to a directory: {raw_value}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
				cwd=repo_root,
			)
		return utils.CheckResult.success(
			f"{var_name} points to {raw_value}",
			stdout=proc.stdout,
			stderr=proc.stderr,
			returncode=proc.returncode,
			cwd=repo_root,
		)

	return (
		DependencyVersionCheck(
			name="git",
			cmd=("git", "--version"),
			min_version=(0, 0, 0),
			timeout_seconds=5.0,
		),
		DependencyVersionCheck(
			name="maven",
			cmd=("mvn", "-v"),
			min_version=(3, 6, 3),
			version_regex=r"Apache Maven\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		DependencyVersionCheck(
			name="gradle",
			cmd=("gradle", "-v"),
			min_version=(4, 4, 1),
			version_regex=r"Gradle\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		DependencyVersionCheck(
			name="ant",
			cmd=("ant", "-version"),
			min_version=(1, 10, 0),
			version_regex=r"version\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		DependencyVersionCheck(
			name="python3",
			cmd=("python3", "--version"),
			min_version=(3, 10, 0),
			version_regex=r"Python\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		DependencyVersionCheck(
			name="java",
			cmd=("java", "-version"),
			min_version=(1, 8, 0),
			max_version=(1, 8, 0),
			version_regex=r'version\s+"([^"]+)"',
			timeout_seconds=5.0,
		),
		DependencyVersionCheck(
			name="tree",
			cmd=("tree", "--version"),
			min_version=(0, 0, 0),
			optional=True,
			timeout_seconds=5.0,
		),
		utils.Check(
			name="WASABI_ROOT_DIR_is_directory",
			fn=lambda: _check_runtime_dir_env("WASABI_ROOT_DIR"),
		),
		FilesystemPathCheck(
			name="wasabi_root_directory_exists",
			path=repo_root,
			path_type=PathType.DIRECTORY,
		),
		utils.Check(
			name="JAVA_HOME_is_directory",
			fn=lambda: _check_runtime_dir_env("JAVA_HOME"),
		),
		FilesystemPathCheck(
			name="benchmarks_directory_exists",
			path=benchmarks_root,
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="config_directory_exists",
			path=repo_root / "config",
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="utils_directory_exists",
			path=repo_root / "utils",
			path_type=PathType.DIRECTORY,
		),
		FilesystemPathCheck(
			name="pom_xml_exists",
			path=repo_root / "pom.xml",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="utils_prereqs_sh_exists",
			path=repo_root / "utils" / "prereqs.sh",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="utils_run_py_exists",
			path=repo_root / "utils" / "run.py",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="utils_display_bug_results_py_exists",
			path=repo_root / "utils" / "display_bug_results.py",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="config_hadoop_example_conf_exists",
			path=repo_root / "config" / "hadoop" / "example.conf",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="config_hadoop_hadoop_conf_exists",
			path=repo_root / "config" / "hadoop" / "hadoop.conf",
			path_type=PathType.FILE,
		),
		FilesystemPathCheck(
			name="config_hadoop_pom_hadoop_xml_exists",
			path=repo_root / "config" / "hadoop" / "pom-hadoop.xml",
			path_type=PathType.FILE,
		),
	)
