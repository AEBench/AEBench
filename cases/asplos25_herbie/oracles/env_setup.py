from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.reporting import BaseCheck


class OracleEnvSetup(CaseOracleEnvSetupBase):
	def requirements(self) -> Sequence[BaseCheck]:
		return (
			*(
				self.version_check(name=tool, cmd=(tool, "--version"), min_version=version)
				for tool, version in (
					("racket", (8, 0, 0)),
					("rustc", (1, 77, 2)),
					("python3", (3, 10, 0)),
					("julia", (1, 10, 0)),
					("clang", (14, 0, 0)),
					("cmake", (3, 0, 0)),
					("make", (3, 81, 0)),
				)
			),
			self.command_check(
				name="python_evaluation_libraries",
				cmd=("python3", "-c", "import numpy, matplotlib, scipy"),
				timeout_seconds=30,
			),
			self.command_check(
				name="vdt_library",
				cmd=(
					"sh",
					"-c",
					"printf '#include <vdt/vdtMath.h>\\nint main() { return 0; }\\n' "
					"| clang++ -x c++ - -lvdt -o /dev/null",
				),
				timeout_seconds=30,
			),
		)
