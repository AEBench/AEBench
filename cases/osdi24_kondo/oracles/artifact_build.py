from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

from evaluator.oracles import utils

_BUILD_MODE_ENV = "AE_KONDO_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0

_EXPECTED_BUILD_OUTPUTS: tuple[str, ...] = (
	"local-dafny/Binaries/Dafny.dll",
	"local-dafny/Scripts/dafny",
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	@staticmethod
	def _build_mode() -> str:
		raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
		return raw or "verify"

	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.paths.workspace_dir

		mode = self._build_mode()

		if mode == "command":
			return (
				BuildCommandCheck(
					name="build_dafny",
					cwd=repo_root / "local-dafny",
					cmd=("make",),
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
			)

		return tuple(
			FilesystemPathCheck(
				name=f"built_{Path(rel).name}",
				path=repo_root / rel,
				path_type=PathType.FILE,
			)
			for rel in _EXPECTED_BUILD_OUTPUTS
		)
