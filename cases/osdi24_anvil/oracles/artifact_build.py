from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.discovery import artifact_build
from evaluator.oracles.utils import Checkable
from models import OracleInput


@artifact_build
def oracle_artifact_build(context: OracleInput) -> Sequence[Checkable]:
	acto_root = context.workspace_dir / "acto"
	return (
		BuildCommandCheck(
			name="acto_make_lib",
			cwd=acto_root,
			cmd=("make", "lib"),
			timeout_seconds=60.0,
		),
	)
