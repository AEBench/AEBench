from __future__ import annotations

from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

_REQUIRED_FILES: tuple[str, ...] = (
    "simulation/sim.py",
    "run_toy_example.sh",
    "run_workload2.sh",
    "run_workload3.sh",
    "profile_time_per_sim.sh",
    "profile_sensitivity_error_in_size.sh",
)

_REQUIRED_DIRS: tuple[str, ...] = (
    "simulation",
    "data",
    "data/PCS_configs",
)


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        reqs: list[BaseCheck] = []

        for rel_path in _REQUIRED_DIRS:
            reqs.append(
                self.path_check(
                    name=f"dir_{rel_path.replace('/', '_')}",
                    path=self.workspace_path(rel_path),
                    kind=PathKind.DIRECTORY,
                )
            )

        for rel_path in _REQUIRED_FILES:
            reqs.append(
                self.path_check(
                    name=f"file_{rel_path.replace('/', '_')}",
                    path=self.workspace_path(rel_path),
                    kind=PathKind.FILE,
                )
            )

        return tuple(reqs)
