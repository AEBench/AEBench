from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	PYTHON_MIN_VERSION,
	README_PATH,
	REQUIREMENTS_PATH,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm a usable python3 is available and the repo was fetched.

	The entrypoint itself (src/run.py) is not checked here: for a pure-Python
	artifact there is no compile step, so existence + a clean import graph IS the
	build signal, which artifact_build verifies via `python3 src/run.py -h`.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		return (
			self.version_check(
				name="python3_version",
				cmd=("python3", "--version"),
				min_version=PYTHON_MIN_VERSION,
			),
			self.path_check(
				name="readme_exists",
				path=self.runtime_path(README_PATH),
				kind=PathKind.FILE,
			),
			self.path_check(
				name="requirements_exists",
				path=self.runtime_path(REQUIREMENTS_PATH),
				kind=PathKind.FILE,
			),
		)
