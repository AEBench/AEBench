from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="chassis_revision",
				cmd=("git", "rev-parse", "HEAD"),
				cwd=self.runtime_path(),
				signature="97c216c931dfc7590a0efd792583fe106d80756e",
				timeout_seconds=30,
			),
			self.command_check(
				name="chassis_evaluation_server",
				cmd=(
					"sh",
					"-c",
					"case $(uname -m) in aarch64|arm64) export NO_AVX=1;; esac; "
					"printf '(exit)\\n' | racket infra/platforms/server.rkt --platform c",
				),
				cwd=self.runtime_path(),
				timeout_seconds=120,
			),
		)
