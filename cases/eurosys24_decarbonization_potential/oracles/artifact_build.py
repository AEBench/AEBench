from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase
from evaluator.oracles.reporting import BaseCheck

from .consts import (
	GLOBAL_MODULES_DIR,
	GLOBAL_MODULES_IMPORTS,
	SCOPED_SCRIPTS,
)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	"""The artifact is pure Python with no compile step or image, so "build" means
	the artifact's own code is in a runnable state: its shared helper library
	(global_modules) imports, and every scoped experiment script parses. (The
	third-party deps are an environment concern, verified in env_setup.)
	"""

	def requirements(self) -> Sequence[BaseCheck]:
		imports = ", ".join(GLOBAL_MODULES_IMPORTS)
		compile_cmd = ("python3", "-m", "py_compile") + tuple(f"{d}/{f}" for d, f in SCOPED_SCRIPTS)
		return (
			self.command_check(
				name="global_modules_importable",
				cmd=(
					"python3",
					"-c",
					f"import sys; sys.path.insert(0, {GLOBAL_MODULES_DIR!r}); import {imports}",
				),
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
			),
			self.command_check(
				name="scoped_scripts_compile",
				cmd=compile_cmd,
				cwd=self.runtime_path(),
				timeout_seconds=120.0,
			),
		)
