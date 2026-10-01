from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleExperimentRunsBase

class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # Uses `docker exec failslow1 grep ...` to search for "differential observability" in `result.txt`.
            self.command_check(
                name="bug_symptom_failslow1",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow1:/apache-zookeeper-3.10.0-SNAPSHOT-bin/result.txt /tmp/failslow1_result.txt && grep -iq 'differential observability' /tmp/failslow1_result.txt"),
                timeout_seconds=30.0,
            ),
            # Same for `failslow2`.
            self.command_check(
                name="bug_symptom_failslow2",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow2:/apache-zookeeper-3.10.0-SNAPSHOT-bin/result.txt /tmp/failslow2_result.txt && grep -iq 'differential observability' /tmp/failslow2_result.txt"),
                timeout_seconds=30.0,
            ),
            # Same for `failslow3`.
            self.command_check(
                name="bug_symptom_failslow3",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow3:/apache-zookeeper-3.10.0-SNAPSHOT-bin/result.txt /tmp/failslow3_result.txt && grep -iq 'differential observability' /tmp/failslow3_result.txt"),
                timeout_seconds=30.0,
            ),
        )