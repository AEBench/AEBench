from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import (
    ElementwiseSimilarityThresholdCheck,
    ListSimilarityCheck,
    PathCheck,
    PathKind,
    SimilarityMetric,
)
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .common import find_outputs_dir

# PDF date format: D:YYYYMMDDHHmmSS followed by Z, +HH'mm', -HH'mm' or nothing
_PDF_CREATION_DATE = re.compile(
    r"/CreationDate\s*\(D:(\d{14})(Z|[+-]\d{2}'?\d{2}'?)?"
)
# PDF dates have one-second resolution
_CLOCK_SLACK = timedelta(seconds=60)


def _pdf_creation_date(
    pdf_path: Path, *, executor: RuntimeCheckExecutor
) -> datetime | None:
    """Returns the CreationDate embedded in a PDF's info dictionary.

    The PDF is binary, so grep extracts the ASCII date instead of decoding
    the whole file as text.
    """
    result = executor.run_process_capture(
        cmd=("grep", "-a", "-o", "-m", "1", "/CreationDate *(D:[0-9Z+'-]*",
             str(executor.resolve_path(pdf_path))),
        cwd=None,
        env=None,
        timeout_seconds=10.0,
    )
    match = _PDF_CREATION_DATE.search(result.stdout or "")
    if result.returncode != 0 or match is None:
        return None

    stamp, offset = match.groups()
    created = datetime.strptime(stamp, "%Y%m%d%H%M%S")
    if offset in (None, "Z"):
        return created.replace(tzinfo=timezone.utc)
    sign = 1 if offset[0] == "+" else -1
    digits = offset[1:].replace("'", "")
    delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return created.replace(tzinfo=timezone(sign * delta))


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfCreatedDuringRunCheck(BaseCheck):
    """Checks that a PDF was generated while the agent was running.

    Outputs plotted earlier, or copied from another run, carry a
    CreationDate outside this run's window. Unlike the file's mtime, the
    embedded date survives copies of the workspace.
    """

    pdf_path: Path
    started_at: datetime | None
    finished_at: datetime | None

    def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
        if self.started_at is None or self.finished_at is None:
            return CheckResult.failure("agent run start/end times are unavailable")
        try:
            created = _pdf_creation_date(self.pdf_path, executor=executor)
        except (OSError, ValueError) as exc:
            return CheckResult.failure(f"failed to read {self.pdf_path}: {exc}")
        if created is None:
            return CheckResult.failure(f"no CreationDate found in {self.pdf_path}")

        # AEBench records run times in UTC
        started, finished = (
            t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            for t in (self.started_at, self.finished_at)
        )
        if not (started - _CLOCK_SLACK <= created <= finished + _CLOCK_SLACK):
            return CheckResult.failure(
                f"{self.pdf_path.name} created at {created.isoformat()}, outside the "
                f"agent run ({started.isoformat()} to {finished.isoformat()})"
            )
        return CheckResult.success(
            f"{self.pdf_path.name} created at {created.isoformat()}"
        )


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
        outputs_dir = (
            find_outputs_dir(self.workspace_path(), executor=self.executor)
            or self.workspace_path() / "outputs"
        )
        checks: list[BaseCheck] = []

        # Check existance of PDFs
        for pdf_name in ["keyswitch_comparison.pdf", "bootstrap_comparison.pdf", "performance.pdf", "performance_per_dollar.pdf"]:
            checks.append(
                PathCheck(
                    name=f"exists_{pdf_name.split('.')[0]}",
                    path=outputs_dir / pdf_name,
                    kind=PathKind.FILE,
                )
            )

        # Check PDFs generated during this agent run (not stale or copied from elsewhere)
        runtime_result = self.context.runtime_result
        started_at = None if runtime_result is None else runtime_result.started_at
        finished_at = None if runtime_result is None else runtime_result.finished_at
        for pdf_name in ["keyswitch_comparison.pdf", "bootstrap_comparison.pdf", "performance.pdf", "performance_per_dollar.pdf"]:
            checks.append(
                PdfCreatedDuringRunCheck(
                    name=f"created_during_run_{pdf_name.split('.')[0]}",
                    pdf_path=outputs_dir / pdf_name,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )

        # Check txt file existance
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
                min_similarity=0.95,
            )
        )
        checks.append(
            ElementwiseSimilarityThresholdCheck(
                name="results_element_similarity",
                observed=observed,
                reference=reference,
                threshold=0.95,
            )
        )

        return tuple(checks)
