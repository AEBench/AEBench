from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind, utils


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
	def requirements(self) -> Sequence[utils.BaseCheck]:
		acto_root = self.workspace_path("acto")
		expected_branch = self.read_text(self.ref_path("acto.expected_branch.txt")).strip()
		expected_head = self.read_text(self.ref_path("acto.expected_head.txt")).strip()

		return (
			self.path_check(
				name="repo_directory_exists",
				path=acto_root,
				kind=PathKind.DIRECTORY,
			),
			self.command_check(
				name="git_working_tree",
				cwd=acto_root,
				cmd=("git", "rev-parse", "--is-inside-work-tree"),
				signature="true",
				timeout_seconds=10.0,
			),
			self.command_check(
				name="on_expected_branch",
				cwd=acto_root,
				cmd=("git", "rev-parse", "--abbrev-ref", "HEAD"),
				signature=expected_branch,
				timeout_seconds=10.0,
			),
			self.command_check(
				name="head_matches_expected",
				cwd=acto_root,
				cmd=("git", "rev-parse", "HEAD"),
				signature=expected_head,
				timeout_seconds=10.0,
			),
		)
