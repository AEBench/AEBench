from __future__ import annotations

import os
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles import PathKind
from evaluator.oracles.bases import CaseOracleArtifactBuildBase

_DEFAULT_CONDA_ENV_NAME = "morphenv"

# Necessary imports
_REQUIRED_IMPORTS = (
    "qiskit, qiskit_aer, qiskit_ibm_runtime, pennylane, pennylane_qiskit, "
    "qiskit_experiments, qiskit_ibm_experiment, autoray, "
    "qiskit_optimization, qiskit_finance, qiskit_nature, "
    "qiskit_dynamics, cv2"
)

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    """Verify the morphenv Conda environment contains the correctly built dependencies and versions."""

    def requirements(self) -> Sequence[BaseCheck]:
        env_name = os.environ.get("AE_MORPHQPV_CONDA_ENV", _DEFAULT_CONDA_ENV_NAME).strip()

        return (
            self.path_check(
                name="requirements_exists",
                path=self.workspace_path("requirements.txt"),
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="requirements_no_version_exists",
                path=self.workspace_path("requirementswithnoversion.txt"),
                kind=PathKind.FILE,
            ),


            self.command_check(
                name=f"conda_env_{env_name}_exists",
                cmd=(
                    "bash", "-c",
                    f"conda env list | grep -qE '^{env_name}[[:space:]]'",
                ),
                timeout_seconds=30.0,
            ),
            # Check Qiskit Version
            self.version_check(
                name="qiskit_version",
                cmd=("conda", "run", "-n", env_name, "python", "-c", "import qiskit; print(qiskit.__version__)"),
                min_version=(0, 25, 1),
            ),
            # Check PennyLane Version
            self.version_check(
                name="pennylane_version",
                cmd=("conda", "run", "-n", env_name, "python", "-c", "import pennylane; print(pennylane.__version__)"),
                min_version=(0, 32, 0),
            ),
            # Check all other dependencies
            self.command_check(
                name="conda_env_packages_importable",
                cmd=(
                    "conda", "run", "-n", env_name,
                    "python", "-c", f"import {_REQUIRED_IMPORTS}",
                ),
                timeout_seconds=60.0,
            ),
        )