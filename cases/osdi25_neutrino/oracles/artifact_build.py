from __future__ import annotations

import sys
from collections.abc import Sequence

from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.discovery import artifact_build
from evaluator.oracles.utils import Checkable
from models import OracleInput


@artifact_build
def oracle_artifact_build(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir
	python_executable = sys.executable or "python"
	return (
		BuildCommandCheck(
			name="neutrino_import_test",
			cwd=repo_root,
			cmd=(python_executable, "-c", "import neutrino; print(neutrino.__file__)"),
			timeout_seconds=30.0,
		),
		BuildCommandCheck(
			name="neutrino_cli_help",
			optional=True,
			cwd=repo_root,
			cmd=("neutrino", "--help"),
			timeout_seconds=30.0,
		),
	)
