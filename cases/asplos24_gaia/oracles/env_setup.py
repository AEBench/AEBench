from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	PYTHON_MIN_VERSION,
	README_PATH,
	REQUIRED_PY_MODULES,
	resolve_python_shell,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
	"""Confirm the environment is provisioned: a usable python3, the fetched repo
	and its dependency manifest, and the third-party deps installed & importable.
	The interpreter is resolved (system python3 or an agent-created venv); see
	consts.resolve_python_shell.
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		checks: list[BaseCheck] = [
			self.command_check(
				name="python3_version",
				cmd=resolve_python_shell()
				+ '"$PY" -c "import sys; assert sys.version_info[:3] >= %r"' % (PYTHON_MIN_VERSION,),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
				use_shell=True,
			),
			self.path_check(
				name="readme_exists",
				path=self.runtime_path(README_PATH),
				kind=PathKind.FILE,
			),
		]

		for module in REQUIRED_PY_MODULES:
			checks.append(
				self.command_check(
					name=f"dep_{module}_importable",
					cmd=resolve_python_shell() + f'"$PY" -c "import {module}"',
					cwd=self.runtime_path(),
					timeout_seconds=120.0,
					use_shell=True,
				)
			)

		return tuple(checks)
