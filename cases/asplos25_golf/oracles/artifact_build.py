from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind

from .consts import BASELINE_BINARY_PATH, GOLF_BINARY_PATH, TESTER_BINARY_PATH


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence:
		return (
			self.path_check(
				name="golf_binary",
				path=self.app_path(GOLF_BINARY_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="baseline_binary",
				path=self.app_path(BASELINE_BINARY_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="tester_binary",
				path=self.app_path(TESTER_BINARY_PATH),
				kind=PathKind.FILE,
			),
		)
