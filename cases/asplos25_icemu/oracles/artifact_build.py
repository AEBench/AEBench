from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import ICEMU_BINARY_PATH, PATCHED_CLANG_PATH, PLUGIN_PATHS


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""Verify the patched compiler, emulator, and experiment plugins were built."""

	def requirements(self) -> Sequence[BaseCheck]:
		outputs = (PATCHED_CLANG_PATH, ICEMU_BINARY_PATH, *PLUGIN_PATHS)
		return tuple(
			self.path_check(
				name=f"built_{Path(relative).stem}",
				path=self.runtime_path(relative),
				kind=PathKind.FILE,
			)
			for relative in outputs
		)
