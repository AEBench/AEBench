from __future__ import annotations

import os
from collections.abc import Sequence

from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.discovery import artifact_build
from evaluator.oracles.utils import Checkable
from models import OracleInput


@artifact_build
def oracle_artifact_build(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	cpu_count = os.cpu_count() or 1
	make_jobs = max(1, cpu_count // 2)
	return (
		BuildCommandCheck(
			name=f"eet_make_j{make_jobs}",
			cwd=repo_root,
			cmd=("make", f"-j{make_jobs}"),
			timeout_seconds=600.0,
		),
	)
