from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

_ANALYSIS_SCRIPTS = (
	"analysis/cold_starts.py",
	"analysis/overhead.py",
	"analysis/overhead_storage.py",
	"analysis/invo_lat.py",
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="scoped_analysis_scripts_compile",
				cmd=(
					"python3",
					"-B",
					"-c",
					"import sys; [compile(open(path, 'rb').read(), path, 'exec') for path in sys.argv[1:]]",
					*_ANALYSIS_SCRIPTS,
				),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
			),
		)
