from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

from .consts import PYTHON_COMMAND


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="powermove_compilers_run",
				cmd=(
					*PYTHON_COMMAND,
					"-B",
					"-c",
					"import math, random\n"
					"from mvqc import mvqc\n"
					"from enola import enola\n"
					"for run_compiler in (lambda: mvqc([[(0, 1)]], 2, 4, True), "
					"lambda: mvqc([[(0, 1)]], 2, 4, False), "
					"lambda: enola([[(0, 1)]], 2, 4)):\n"
					"    random.seed(0)\n"
					"    result = run_compiler()\n"
					"    if len(result) != 9 or not all(math.isfinite(v) for v in result):\n"
					"        raise SystemExit('invalid compiler smoke output')\n"
					"print('PowerMove and Enola compilation passed')",
				),
				cwd=self.runtime_path(),
				signature="PowerMove and Enola compilation passed",
				timeout_seconds=120.0,
			),
		)
