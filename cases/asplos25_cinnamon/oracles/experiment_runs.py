from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import (
    ListSimilarityCheck,
    PathCheck,
    PathKind,
    SimilarityMetric,
)
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck


def _extract_floats_from_txt(
    txt_path: Path, *, executor: RuntimeCheckExecutor
) -> list[float]:
    """Parses the generated markdown-style table and extracts all numeric values."""
    if not executor.path_exists(txt_path):
        return []
        
    observed_values = []
    content = executor.read_file_text(txt_path)
    
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|") or "Benchmark" in line or "---" in line:
            continue
            
        cols = [col.strip() for col in line.split("|")[1:-1]]
        for val in cols[1:]: # Skip the benchmark name column
            if val and val != "-":
                try:
                    observed_values.append(float(val))
                except ValueError:
                    pass
                    
    return observed_values

def _load_reference_floats(
    ref_path: Path, *, executor: RuntimeCheckExecutor
) -> list[float]:
    """Loads the reference JSON and flattens it into a list of floats."""
    data = json.loads(executor.read_file_text(ref_path))
    return [float(v) for benchmark_data in data.values() for v in benchmark_data.values()]


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        outputs_dir = self.workspace_path() / "outputs"
        checks: list[BaseCheck] = []

        #existance of pdfs
        for pdf_name in ["keyswitch_comparison.pdf", "bootstrap_comparison.pdf", "performance.pdf", "performance_per_dollar.pdf"]:
            checks.append(
                PathCheck(
                    name=f"exists_{pdf_name.split('.')[0]}",
                    path=outputs_dir / pdf_name,
                    kind=PathKind.FILE,
                )
            )

        #txt file existance
        txt_path = outputs_dir / "performance_table.txt"
        checks.append(
            PathCheck(
                name="exists_performance_table",
                path=txt_path,
                kind=PathKind.FILE,
            )
        )

        
        observed = _extract_floats_from_txt(txt_path, executor=self.executor)
        reference = _load_reference_floats(
            self.ref_path("results.json"), executor=self.executor
        )

        checks.append(
            ListSimilarityCheck(
                name="results_correlation",
                observed=observed,
                reference=reference,
                metric=SimilarityMetric.PEARSON,
                min_similarity=0.75,
            )
        )

        return tuple(checks)
