from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles import CaseOracleArtifactBuildBase, CommandCheck, VersionCheck
from evaluator.oracles.reporting import BaseCheck

 # All installed packages
modules = (
    "autoray, bqskit, cv2, gurobipy, jax, jaxlib, matplotlib, networkx, "
    "numpy, openai, optax, pandas, pennylane, pennylane_qiskit, qiskit, "
    "qiskit_aer, qiskit_dynamics, qiskit_experiments, qiskit_finance, "
    "qiskit_ibm_experiment, qiskit_ibm_runtime, qiskit_nature, "
    "qiskit_optimization, ray, sklearn, torchvision, tqdm, z3"
)

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    """Verify the morphenv Conda environment contains the correctly built dependencies and versions."""

    def requirements(self) -> Sequence[BaseCheck]:
        checks: list[BaseCheck] = []

        # Version check for Qiskit base framework (specifc versioning needed)
        checks.append(
            VersionCheck(
                name="qiskit_version",
                cmd=("conda", "run", "-n", "morphenv", "python", "-c", "import qiskit; print(qiskit.__version__)"),
                min_version=(0, 44, 1),
            )
        )

        # PennyLane requirements (specific versioning needed)
        checks.append(
            VersionCheck(
                name="pennylane_version",
                cmd=("conda", "run", "-n", "morphenv", "python", "-c", "import pennylane; print(pennylane.__version__)"),
                min_version=(0, 32, 0),
            )
        )

       
        # Rest of the checks, version not specific
        checks.append(
            CommandCheck(
                name="python_deps_importable",
                cmd=("conda", "run", "-n", "morphenv", "python", "-c", f"import {modules}"),
                timeout_seconds=60.0,
            )
        )

        return tuple(checks)
