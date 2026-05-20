from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.benchmark_prep_checks import BenchmarkCommandCheck
from evaluator.oracles.discovery import benchmark_prep
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from evaluator.oracles.utils import Checkable
from models import OracleInput


@benchmark_prep
def oracle_benchmark_prep(context: OracleInput) -> Sequence[Checkable]:
	acto_root = context.workspace_dir / "acto"
	refs_dir = context.case_dir / "refs"
	expected_branch = (refs_dir / "acto.expected_branch.txt").read_text(encoding="utf-8").strip()
	expected_head = (refs_dir / "acto.expected_head.txt").read_text(encoding="utf-8").strip()

	return (
		FilesystemPathCheck(
			name="repo_directory_exists",
			path=acto_root,
			path_type=PathType.DIRECTORY,
		),
		BenchmarkCommandCheck(
			name="git_working_tree",
			cwd=acto_root,
			cmd=("git", "rev-parse", "--is-inside-work-tree"),
			signature="true",
			timeout_seconds=10.0,
		),
		BenchmarkCommandCheck(
			name="on_expected_branch",
			cwd=acto_root,
			cmd=("git", "rev-parse", "--abbrev-ref", "HEAD"),
			signature=expected_branch,
			timeout_seconds=10.0,
		),
		BenchmarkCommandCheck(
			name="head_matches_expected",
			cwd=acto_root,
			cmd=("git", "rev-parse", "HEAD"),
			signature=expected_head,
			timeout_seconds=10.0,
		),
	)
