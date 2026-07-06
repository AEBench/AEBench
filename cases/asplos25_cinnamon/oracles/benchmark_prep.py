from __future__ import annotations

from collections.abc import Sequence
from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import CommandCheck, PathCheck, PathKind

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        checks: list[utils.BaseCheck] = []

        #output folder created
        checks.append(
            PathCheck(
                name="local_outputs_dir_exists",
                path=self.workspace_path() / "outputs",
                kind=PathKind.DIRECTORY,
            )
        )


        #container was actually started with the correct -v flag linking the outputs folder.
        checks.append(
            CommandCheck(
                name="outputs_volume_mounted",
                cmd=("sh", "-c", "docker inspect cinnamon | grep -q '/cinnamon_artifact/outputs'"),
                timeout_seconds=10.0,
            )
        )

        #required scripts exist inside the container
        checks.append(
            CommandCheck(
                name="cinnamon_scripts_ready",
                cmd=(
                    "docker", "exec", "cinnamon", "sh", "-c", 
                    "ls build_cinnamon.sh run_keyswitch_comparison.sh run_bootstrap_comparison.sh run_performance.sh"
                ),
                timeout_seconds=10.0,
            )
        )
        #
        checks.append(
            CommandCheck(
                name="verify_cinnamon_build_artifacts",
                cmd=(
                    "docker", "exec", "cinnamon", "sh", "-c", 
                    "test -f /cinnamon_artifact/simulator/build/install/custom-elements/lib/libcinnamon.so"
                ),
                timeout_seconds=10.0,
            )
        )
        return tuple(checks)