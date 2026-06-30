from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind, utils


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		anvil_root = self.workspace_path()
		acto_root = self.workspace_path("acto")

		return (
			self.path_check(
				name="workspace_root_exists",
				path=self.workspace_path(),
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="repo_osdi24_anvil",
				path=anvil_root,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="repo_osdi24_acto_dependency",
				path=acto_root,
				kind=PathKind.DIRECTORY,
			),
			self.path_check(
				name="repo_osdi24_anvil_cargo_toml",
				path=anvil_root / "Cargo.toml",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="repo_osdi24_acto_makefile",
				path=acto_root / "Makefile",
				kind=PathKind.FILE,
			),
			self.path_check(
				name="ref_table3",
				path=self.ref_path("anvil-table-3.ref.json"),
				kind=PathKind.FILE,
			),
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=(3, 10, 0),
			),
		)
