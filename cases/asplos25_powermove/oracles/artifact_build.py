from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

# PowerMove is pure Python with no build step: the agent's environment must import both
# compilers and the QASM parser the test_scripts use from the repository root.
_IMPORTS = "import mvqc, enola, qiskit.qasm2; print('OK mvqc enola qiskit.qasm2 import')"


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.command_check(
				name="powermove_import",
				cmd=("python3", "-c", _IMPORTS),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
			),
		)
