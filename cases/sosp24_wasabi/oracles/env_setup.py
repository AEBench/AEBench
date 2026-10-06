from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import (
	CaseOracleEnvSetupBase,
	PathKind,
)
from evaluator.oracles.oracle_checks_runtime import (
	RuntimeCheckExecutor,
	read_check_env_var,
	run_check_process_capture,
)
from evaluator.oracles.reporting import BaseCheck, Check, CheckResult


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		repo_root = self.workspace_path()
		benchmarks_root = self.workspace_path("benchmarks")

		def _check_runtime_dir_env(executor: RuntimeCheckExecutor, var_name: str) -> CheckResult:
			try:
				raw_value = read_check_env_var(var_name, executor=executor)
			except (RuntimeError, ValueError) as exc:
				return CheckResult.failure(str(exc))
			if raw_value is None or not raw_value.strip():
				return CheckResult.failure(f"{var_name} is not set")
			proc = run_check_process_capture(
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
				return CheckResult.failure(
					f"{var_name} directory check timed out",
					stdout=proc.stdout,
					stderr=proc.stderr,
					timed_out=True,
					cwd=repo_root,
				)
			if proc.returncode != 0:
				return CheckResult.failure(
					f"{var_name} does not point to a directory: {raw_value}",
					stdout=proc.stdout,
					stderr=proc.stderr,
					returncode=proc.returncode,
					cwd=repo_root,
				)
			return CheckResult.success(
				f"{var_name} points to {raw_value}",
				stdout=proc.stdout,
				stderr=proc.stderr,
				returncode=proc.returncode,
				cwd=repo_root,
			)

		return (
			self.version_check(
				name="git",
				cmd=("git", "--version"),
				min_version=(0, 0, 0),
				timeout_seconds=5.0,
			),
			self.version_check(
				name="maven",
				cmd=("mvn", "-v"),
				min_version=(3, 6, 3),
				version_regex=r"Apache Maven\s+([0-9.]+)",
				timeout_seconds=5.0,
			),
			self.version_check(
				name="gradle",
				cmd=("gradle", "-v"),
				min_version=(4, 4, 1),
				version_regex=r"Gradle\s+([0-9.]+)",
				timeout_seconds=5.0,
			),
			self.version_check(
				name="ant",
				cmd=("ant", "-version"),
				min_version=(1, 10, 0),
				version_regex=r"version\s+([0-9.]+)",
				timeout_seconds=5.0,
			),
			self.version_check(
				name="python3",
				cmd=("python3", "--version"),
				min_version=(3, 10, 0),
				version_regex=r"Python\s+([0-9.]+)",
				timeout_seconds=5.0,
			),
			self.version_check(
				name="java",
				cmd=("java", "-version"),
				min_version=(1, 8, 0),
				max_version=(1, 8, 0),
				version_regex=r'version\s+"([^"]+)"',
				timeout_seconds=5.0,
			),
			self.version_check(
				name="tree",
				cmd=("tree", "--version"),
				min_version=(0, 0, 0),
				optional=True,
				timeout_seconds=5.0,
			),
			Check(
				name="WASABI_ROOT_DIR_is_directory",
				fn=lambda executor: _check_runtime_dir_env(executor, "WASABI_ROOT_DIR"),
			),
			self.path_check(
				name="wasabi_root_directory_exists",
				path=repo_root,
				kind=PathKind.DIRECTORY,
			),
			Check(
				name="JAVA_HOME_is_directory",
				fn=lambda executor: _check_runtime_dir_env(executor, "JAVA_HOME"),
			),
			self.path_check(
				name="benchmarks_directory_exists",
				path=benchmarks_root,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="config_directory_exists",
				path=repo_root / "config",
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="utils_directory_exists",
				path=repo_root / "utils",
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="pom_xml_exists",
				path=repo_root / "pom.xml",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="utils_prereqs_sh_exists",
				path=repo_root / "utils" / "prereqs.sh",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="utils_run_py_exists",
				path=repo_root / "utils" / "run.py",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="utils_display_bug_results_py_exists",
				path=repo_root / "utils" / "display_bug_results.py",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="config_hadoop_example_conf_exists",
				path=repo_root / "config" / "hadoop" / "example.conf",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="config_hadoop_hadoop_conf_exists",
				path=repo_root / "config" / "hadoop" / "hadoop.conf",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="config_hadoop_pom_hadoop_xml_exists",
				path=repo_root / "config" / "hadoop" / "pom-hadoop.xml",
				kind=PathKind.FILE,
			),
		)
