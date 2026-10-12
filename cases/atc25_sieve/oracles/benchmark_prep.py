from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # Uses `docker exec failslow4` to check if a specific `.jar` file exists inside the `failslow4` container, which the author comments "guarantees `reproduce_zk4_server.sh` was run successfully."
            self.command_check(
                name="engine_started",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow4:/failslow/target/failslow-1.0-SNAPSHOT-jar-with-dependencies.jar - > /dev/null"),
                timeout_seconds=30.0,
            ),
        )