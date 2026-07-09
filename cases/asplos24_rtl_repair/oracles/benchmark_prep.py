from __future__ import annotations

import re
from collections.abc import Sequence

from evaluator.oracles.bases import CaseOracleBenchmarkPrepBase
from evaluator.oracles.checks import PathKind
from evaluator.oracles.reporting import BaseCheck

_BENCHMARK_MANIFEST = {
    "required_directories": (
        "benchmarks/cirfix",
        "benchmarks/fpga-debugging",
        "cirfix",
        "scripts",
        "rtlrepair",
        "synth",
    ),
    "required_files": (
        "rtlrepair.py",
        "scripts/run_rtl_repair_experiment.py",
        "scripts/check_repairs.py",
        "scripts/create_tables.py",
    ),
}


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[BaseCheck]:
        reqs: list[BaseCheck] = []

        for rel_path in _BENCHMARK_MANIFEST["required_directories"]:
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", rel_path).strip("_") or "root"
            reqs.append(
                self.path_check(
                    name=f"dir_{safe_name}",
                    path=self.artifact_path(rel_path),
                    kind=PathKind.DIRECTORY,
                )
            )

        for rel_path in _BENCHMARK_MANIFEST["required_files"]:
            safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", rel_path).strip("_") or "file"
            reqs.append(
                self.path_check(
                    name=f"file_{safe_name}",
                    path=self.artifact_path(rel_path),
                    kind=PathKind.FILE,
                )
            )

        return tuple(reqs)
