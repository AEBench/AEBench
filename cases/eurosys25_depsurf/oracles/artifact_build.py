from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.discovery import artifact_build
from evaluator.oracles.utils import Checkable
from models import OracleInput


@artifact_build
def oracle_artifact_build(context: OracleInput) -> Sequence[Checkable]:
	repo_root = context.workspace_dir

	return (
		BuildCommandCheck(
			name="depsurf_uv_version",
			cwd=repo_root,
			cmd=("uv", "--version"),
			timeout_seconds=60.0,
		),
		BuildCommandCheck(
			name="depsurf_uv_import",
			cwd=repo_root,
			cmd=("uv", "run", "python", "-c", "import depsurf; print('OK depsurf import')"),
			timeout_seconds=60.0,
		),
		BuildCommandCheck(
			name="depsurf_uv_python_minimal",
			cwd=repo_root,
			cmd=("uv", "run", "python", "-c", "print('OK python')"),
			timeout_seconds=60.0,
			optional=True,
		),
		BuildCommandCheck(
			name="depsurf_jupyter_lab_version",
			cwd=repo_root,
			cmd=("uv", "run", "jupyter", "lab", "--version"),
			timeout_seconds=60.0,
		),
		# eurosys25 branch builds bpftool; old bcc/tracee subtrees are not in this AE path.
		BuildCommandCheck(
			name="depsurf_make_bpftool",
			cwd=repo_root,
			cmd=("make", "-C", "depsurf/btf/bpftool/src", "bpftool"),
			timeout_seconds=1800.0,
		),
		BuildCommandCheck(
			name="depsurf_bpftool_version",
			cwd=repo_root,
			cmd=("./depsurf/btf/bpftool/src/bpftool", "version"),
			timeout_seconds=60.0,
		),
	)
