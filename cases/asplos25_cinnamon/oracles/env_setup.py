from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleEnvSetupBase
from evaluator.oracles.checks import (
    VersionCheck,
    PathCheck,
    PathKind,
)
#from .common import RepoRootLocatedCheck, find_repo_root


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        #repo_root = self.workspace_path();
        

        return (
            VersionCheck(
                name="docker",
                cmd=("docker", "--version"),
                min_version=(20, 10, 0),
                
            ),
            VersionCheck(
                name="bash",
                cmd=("bash", "--version"),
                min_version=(4,0,0),
                optional= True,
            ),
        )
