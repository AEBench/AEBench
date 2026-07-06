from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import (
    VersionCheck,
)


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            VersionCheck(
                name="docker",
                cmd=("docker", "--version"),
                min_version=(20, 10, 0),
                
            ),
        )
