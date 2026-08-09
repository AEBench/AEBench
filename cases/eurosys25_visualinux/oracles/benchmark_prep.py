from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathCheck, PathKind

class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # 1. Verify that the agent downloaded and extracted busybox
            PathCheck(
                name="busybox_prepared",
                path=self.workspace_path("workload", "busybox"),
                kind=PathKind.DIRECTORY,
            ),
            # 2. Verify that the agent cloned liburing for the workload
            PathCheck(
                name="liburing_prepared",
                path=self.workspace_path("workload", "src", "io_uring", "liburing"),
                kind=PathKind.DIRECTORY,
            ),
            # 3. Verify that the agent compiled the agent-proxy utility
            PathCheck(
                name="agent_proxy_compiled",
                path=self.workspace_path("scripts", "kgdb", "agent-proxy", "agent-proxy"),
                kind=PathKind.FILE,
            ),
            # 4. Verify that the agent installed the visualizer dependencies
            PathCheck(
                name="visualizer_node_modules_exists",
                path=self.workspace_path("visualizer", "node_modules"),
                kind=PathKind.DIRECTORY,
            ),
        )