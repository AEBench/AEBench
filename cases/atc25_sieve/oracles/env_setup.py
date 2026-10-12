from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleEnvSetupBase

class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # Checks if the Docker version is >= 20.10.0 using `docker --version`.
            self.version_check(
                name="docker",
                cmd=("docker", "--version"),
                min_version=(20, 10, 0),
            ),
        )
