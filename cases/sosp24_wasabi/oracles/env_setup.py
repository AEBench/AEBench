from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathCheck, PathKind, VersionCheck, reporting
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor, RuntimePath
from evaluator.oracles.reporting import BaseCheck
from models import OracleInput

from .common import find_eval_dir, missing_dir_check, shell_env_var


def oracle_env_setup(context: OracleInput, executor: RuntimeCheckExecutor) -> Sequence[BaseCheck]:
	repo_root = context.workspace_dir
	testing_root = repo_root / "wasabi-testing"
	benchmarks_root, searched = find_eval_dir(executor, repo_root, "benchmarks")

	def _check_wasabi_root_dir(executor: RuntimeCheckExecutor) -> reporting.CheckResult:
		value = shell_env_var(executor, "WASABI_ROOT_DIR")
		if value is None:
			return reporting.CheckResult.failure("WASABI_ROOT_DIR is not set")
		if not executor.path_is_file(
			RuntimePath.from_parts(value, "wasabi-testing", "utils", "run.py")
		):
			return reporting.CheckResult.failure(
				f"WASABI_ROOT_DIR={value} does not contain wasabi-testing/utils/run.py"
			)
		return reporting.CheckResult.success(f"WASABI_ROOT_DIR points to {value}")

	def _check_java_home(executor: RuntimeCheckExecutor) -> reporting.CheckResult:
		value = shell_env_var(executor, "JAVA_HOME")
		if value is None:
			return reporting.CheckResult.failure("JAVA_HOME is not set")
		java = executor.resolve_path(RuntimePath.from_parts(value, "bin", "java"))
		return VersionCheck(
			name="JAVA_HOME_is_java_8",
			cmd=(str(java), "-version"),
			min_version=(1, 8, 0),
			max_version=(1, 8, 0),
			version_regex=r'version\s+"([^"]+)"',
			timeout_seconds=5.0,
		).check(executor)

	return (
		VersionCheck(
			name="git",
			cmd=("git", "--version"),
			min_version=(0, 0, 0),
			timeout_seconds=5.0,
		),
		VersionCheck(
			name="maven",
			cmd=("mvn", "-v"),
			min_version=(3, 6, 3),
			version_regex=r"Apache Maven\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		# Only Elasticsearch builds with Gradle, and it is not scored.
		VersionCheck(
			name="gradle",
			cmd=("gradle", "-v"),
			min_version=(4, 4, 1),
			version_regex=r"Gradle\s+([0-9.]+)",
			optional=True,
			timeout_seconds=5.0,
		),
		VersionCheck(
			name="ant",
			cmd=("ant", "-version"),
			min_version=(1, 10, 0),
			version_regex=r"version\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		VersionCheck(
			name="python3",
			cmd=("python3", "--version"),
			min_version=(3, 10, 0),
			version_regex=r"Python\s+([0-9.]+)",
			timeout_seconds=5.0,
		),
		VersionCheck(
			name="java",
			cmd=("java", "-version"),
			min_version=(1, 8, 0),
			max_version=(1, 8, 0),
			version_regex=r'version\s+"([^"]+)"',
			timeout_seconds=5.0,
		),
		VersionCheck(
			name="tree",
			cmd=("tree", "--version"),
			min_version=(0, 0, 0),
			optional=True,
			timeout_seconds=5.0,
		),
		reporting.Check(name="WASABI_ROOT_DIR_is_wasabi_root", fn=_check_wasabi_root_dir),
		PathCheck(
			name="wasabi_root_directory_exists",
			path=repo_root,
			kind=PathKind.DIRECTORY,
		),
		reporting.Check(name="JAVA_HOME_is_java_8", fn=_check_java_home),
		PathCheck(
			name="benchmarks_directory_exists",
			path=benchmarks_root,
			kind=PathKind.DIRECTORY,
		)
		if benchmarks_root is not None
		else missing_dir_check("benchmarks_directory_exists", searched),
		PathCheck(
			name="config_directory_exists",
			path=testing_root / "config",
			kind=PathKind.DIRECTORY,
		),
		PathCheck(
			name="utils_directory_exists",
			path=testing_root / "utils",
			kind=PathKind.DIRECTORY,
		),
		PathCheck(
			name="pom_xml_exists",
			path=testing_root / "pom.xml",
			kind=PathKind.FILE,
		),
		PathCheck(
			name="utils_prereqs_sh_exists",
			path=testing_root / "utils" / "prereqs.sh",
			kind=PathKind.FILE,
		),
		PathCheck(
			name="utils_run_py_exists",
			path=testing_root / "utils" / "run.py",
			kind=PathKind.FILE,
		),
		PathCheck(
			name="utils_display_bug_results_py_exists",
			path=testing_root / "utils" / "display_bug_results.py",
			kind=PathKind.FILE,
		),
		PathCheck(
			name="config_hadoop_example_conf_exists",
			path=testing_root / "config" / "hadoop" / "example.conf",
			kind=PathKind.FILE,
		),
		PathCheck(
			name="config_hadoop_hadoop_conf_exists",
			path=testing_root / "config" / "hadoop" / "hadoop.conf",
			kind=PathKind.FILE,
		),
		PathCheck(
			name="config_hadoop_pom_hadoop_xml_exists",
			path=testing_root / "config" / "hadoop" / "pom-hadoop.xml",
			kind=PathKind.FILE,
		),
	)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return oracle_env_setup(self.context, self.executor)
