from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from evaluator.oracles import utils
from evaluator.oracles.checks import CommandCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase
from evaluator.oracles.checks import PathCheck, PathKind, VersionCheck


_CONDA_ENV_NAME_ENV = "AE_PPIPE_CONDA_ENV"
_DEFAULT_CONDA_ENV_NAME = "ppipe"
_BUILD_MODE_ENV = "AE_PPIPE_BUILD_MODE"
_BUILD_TIMEOUT_SECONDS = 600.0



_BUILD_TIMEOUT_SECONDS = 1800.0
_QUICK_PROBE_TIMEOUT_SECONDS = 30.0
_LICENSE_PROBE_TIMEOUT_SECONDS = 15.0

_SIMULATOR_BINARY = "cluster-sim/build/install/cluster-sim/bin/cluster-sim"

_REQUIRED_IMPORTS = "gurobipy, pandas, numpy, matplotlib, fire"



@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidBuildModeCheck(utils.BaseCheck):
	mode: str

	def check(self) -> utils.CheckResult:
		return utils.CheckResult.failure(
			f"invalid {_BUILD_MODE_ENV}={self.mode!r}; expected 'verify' or 'command'"
		)


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
	@staticmethod
	def _build_mode() -> str:
		raw = os.environ.get(_BUILD_MODE_ENV, "verify").strip().lower()
		return raw or "verify"
	@staticmethod
	def _conda_env_name() -> str:
		raw = os.environ.get(_CONDA_ENV_NAME_ENV, _DEFAULT_CONDA_ENV_NAME).strip()
		return raw or _DEFAULT_CONDA_ENV_NAME

	def requirements(self) -> Sequence[utils.BaseCheck]:
		repo_root = self.workspace_path()
		simulator_binary = repo_root / _SIMULATOR_BINARY

		env_name = self._conda_env_name()
		mode = self._build_mode()

		if mode == "command":
			return (
				CommandCheck(
					name="gradle_install_dist",
					cwd=repo_root,
					cmd=(
						"bash",
						"-c", 
						#covers conda build, python version check, gurobi, and change into java simulator folder
						"conda create -n ppipe python=3.12 -y && conda run -n ppipe pip install -r requirements.txt && cd cluster-sim && ./gradlew installDist",
					),
					timeout_seconds=_BUILD_TIMEOUT_SECONDS,
				),
				PathCheck(
					name="simulator_binary",
					path=simulator_binary,
					kind=PathKind.FILE,
				),
			)

		if mode == "verify":
			return (
				CommandCheck(
                    name=f"conda_env_{env_name}_exists",
                    cmd=(
                        "bash", "-c",
                        f"conda env list | grep -qE '^{env_name}[[:space:]]'",
                    ),
                    timeout_seconds=_QUICK_PROBE_TIMEOUT_SECONDS,
                ),
                VersionCheck(
                    name="conda_env_python_version",
                    cmd=("conda", "run", "-n", env_name, "python", "--version"),
                    min_version=(3, 12, 0),
                ),
                CommandCheck(
                    name="conda_env_packages_importable",
                    cmd=(
                        "conda", "run", "-n", env_name,
                        "python", "-c", f"import {_REQUIRED_IMPORTS}",
                    ),
                    timeout_seconds=_QUICK_PROBE_TIMEOUT_SECONDS,
                ),
                CommandCheck(
                    name="gurobi_license_valid",
                    cmd=(
                        "conda", "run", "-n", env_name,
                        "python", "-c", "import gurobipy; gurobipy.Model('test')",
                    ),
                    timeout_seconds=_LICENSE_PROBE_TIMEOUT_SECONDS,
                ),
				CommandCheck(
					name="java_compatible_with_gradle",
					cwd=repo_root / "cluster-sim",
					cmd=("./gradlew" , "tasks", "--quiet"),
   					timeout_seconds=120.0,
				),
				PathCheck(
					name="simulator_binary",
					path=simulator_binary,
					kind=PathKind.FILE,
				),
			)

		return (
			InvalidBuildModeCheck(
				name="build_mode_valid",
				mode=mode,
			),
		)