from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind, utils


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		workspace = self.workspace_path()

		return (
			self.command_check(
				name="linux_host",
				cmd=("uname", "-s"),
				timeout_seconds=10.0,
				signature="Linux",
			),
			self.version_check(
				name="docker_version",
				cmd=("docker", "--version"),
				min_version=(20, 0, 0),
			),
			self.command_check(
				name="docker_daemon",
				cmd=("docker", "info"),
				timeout_seconds=20.0,
			),
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 8, 0),
			),
			self.version_check(
				name="pip3_version",
				cmd=("pip3", "--version"),
				min_version=(20, 0, 0),
			),
			self.version_check(
				name="go_version",
				cmd=("go", "version"),
				min_version=(1, 18, 0),
				version_regex=r"go(\d+\.\d+(?:\.\d+)?)",
			),
			self.version_check(
				name="kind_version",
				cmd=("kind", "version"),
				min_version=(0, 14, 0),
			),
			self.version_check(
				name="kubectl_version",
				cmd=("kubectl", "version", "--client", "-o", "json"),
				min_version=(1, 22, 9),
				version_regex=r'"gitVersion":\s*"v?(\d+\.\d+(?:\.\d+)?)"',
			),
			self.path_check(
				name="workspace_root",
				path=workspace,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="artifact_readme",
				path=workspace / "README.md",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="artifact_makefile",
				path=workspace / "Makefile",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="python_requirements",
				path=workspace / "requirements.txt",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="campaign_data_gitmodules",
				path=workspace / ".gitmodules",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="k8sutil_makefile",
				path=workspace / "acto" / "k8s_util" / "lib" / "Makefile",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="ssa_makefile",
				path=workspace / "ssa" / "Makefile",
				kind=PathKind.FILE,
			),
		)
