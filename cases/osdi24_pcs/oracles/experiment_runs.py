from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import (
    ListSimilarityCheck,
    PathKind,
    SimilarityMetric,
)

_log = logging.getLogger(__name__)


_POLICY_MAP_TO_REF = {
    "AFS": "AFS",
    "SRSF": "Tiresias",
    "THEMIS": "Themis",
    "FIFO": "FIFO",
    "PCS_jct": "PCS-JCT",
    "PCS_bal": "PCS-bal",
    "PCS_pred": "PCS-pred",
}

_POLICIES = tuple(_POLICY_MAP_TO_REF.keys())
_WORKLOAD2_TRACES = ("0e4a51", "ee9e8c")

_TOY_CSVS = tuple(f"new_data/{policy}_themis1_result.csv" for policy in _POLICIES)
_GAVEL_CSVS = tuple(f"new_data/{policy}_gavel_result.csv" for policy in _POLICIES)

_JCT_SIMILARITY = 0.85
_ERROR_SIMILARITY = 0.80


def _workload2_csvs() -> tuple[str, ...]:
    return tuple(
        f"new_data/{policy}_{trace}_result.csv"
        for trace in _WORKLOAD2_TRACES
        for policy in _POLICIES
    )


def _parse_raw_csv(path: Path) -> dict[str, float]:
    """Parse a simulator CSV and compute JCT and prediction-error metrics."""
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.strip().splitlines())
    rows = list(reader)

    if not rows:
        raise ValueError(f"empty CSV: {path}")

    jcts: list[float] = []
    errors: list[float] = []

    for row in rows:
        submit = float(row["submit_time"])
        end = float(row["end_time"])
        jct = end - submit

        if jct <= 0:
            continue

        jcts.append(jct)

        est_end = float(row["estimated_end_time"])
        est_start = float(row["estimated_start_time"])

        if est_end == -1 and est_start == -1:
            continue

        pred_jct = est_end - submit
        if pred_jct > 0:
            errors.append(100.0 * abs(pred_jct - jct) / pred_jct)

    if not jcts:
        raise ValueError(f"no valid JCT rows in {path}")

    jcts_sorted = sorted(jcts)
    avg_jct = sum(jcts) / len(jcts)
    p99_idx = min(int(len(jcts_sorted) * 0.99), len(jcts_sorted) - 1)
    p99_jct = jcts_sorted[p99_idx]

    avg_error = 0.0
    p99_error = 0.0

    if errors:
        errors_sorted = sorted(errors)
        avg_error = sum(errors) / len(errors)
        p99_idx_e = min(int(len(errors_sorted) * 0.99), len(errors_sorted) - 1)
        p99_error = errors_sorted[p99_idx_e]

    return {
        "avg_jct": avg_jct,
        "p99_jct": p99_jct,
        "avg_error": avg_error,
        "p99_error": p99_error,
    }


def _load_reference_csv(path: Path) -> dict[str, dict[str, float]]:
    """Load a reference CSV into {workload: {policy: value}}."""
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.strip().splitlines())

    result: dict[str, dict[str, float]] = {}

    for row in reader:
        trace = row.get("workload", "").strip()
        if not trace:
            continue

        result[trace] = {}

        for column, value in row.items():
            column = column.strip()

            if column == "workload":
                continue

            try:
                result[trace][column] = float(value)
            except (TypeError, ValueError):
                _log.debug(
                    "skipping non-numeric reference value in %s "
                    "(workload=%s, column=%s, value=%r)",
                    path,
                    trace,
                    column,
                    value,
                )

    return result


@dataclass(frozen=True, slots=True, kw_only=True)
class NonEmptyFileCheck(utils.BaseCheck):
    path: Path

    def check(self) -> utils.CheckResult:
        if not self.path.is_file():
            return utils.CheckResult.failure(f"file missing: {self.path}")

        try:
            size = self.path.stat().st_size
        except OSError as exc:
            return utils.CheckResult.failure(f"cannot stat {self.path}: {exc}")

        if size == 0:
            return utils.CheckResult.failure(f"file is empty: {self.path}")

        return utils.CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class SimulationMetricCorrelationCheck(utils.BaseCheck):
    """Compare computed simulator metrics against reference values via Pearson similarity."""

    new_data_dir: Path
    reference_path: Path
    traces: tuple[str, ...]
    metric: str
    threshold: float
    normalize_jct: bool = False

    def check(self) -> utils.CheckResult:
        try:
            ref_data = _load_reference_csv(self.reference_path)
        except (OSError, ValueError) as exc:
            return utils.CheckResult.failure(f"cannot load reference {self.reference_path}: {exc}")

        observed: list[float] = []
        reference: list[float] = []

        for trace in self.traces:
            if trace not in ref_data:
                continue

            raw_metrics: dict[str, float] = {}

            for policy in _POLICIES:
                csv_path = self.new_data_dir / f"{policy}_{trace}_result.csv"

                if not csv_path.is_file():
                    continue

                try:
                    metrics = _parse_raw_csv(csv_path)
                    raw_metrics[policy] = metrics[self.metric]
                except (OSError, ValueError, KeyError) as exc:
                    _log.warning(
                        "skipping %s for trace %s: %s: %s",
                        policy,
                        trace,
                        type(exc).__name__,
                        exc,
                    )

            if self.normalize_jct and raw_metrics:
                min_val = min(raw_metrics.values())
                if min_val > 0:
                    raw_metrics = {
                        policy: value / min_val
                        for policy, value in raw_metrics.items()
                    }

            for policy, ref_name in _POLICY_MAP_TO_REF.items():
                obs_val = raw_metrics.get(policy)
                ref_val = ref_data.get(trace, {}).get(ref_name)

                if obs_val is not None and ref_val is not None:
                    observed.append(obs_val)
                    reference.append(ref_val)

        if len(observed) < 3:
            return utils.CheckResult.failure(
                f"too few data points for {self.metric} correlation "
                f"(found {len(observed)}, need at least 3)"
            )

        delegated = ListSimilarityCheck(
            name=self.name,
            optional=self.optional,
            observed=observed,
            reference=reference,
            metric=SimilarityMetric.PEARSON,
            min_similarity=self.threshold,
        )
        return delegated.check()


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        new_data = self.workspace_path("new_data")

        reqs: list[utils.BaseCheck] = [
            self.path_check(
                name="new_data_dir",
                path=new_data,
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="ref_avg_jct_results",
                path=self.ref_path("avg_jct_results.csv"),
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="ref_avg_error_results",
                path=self.ref_path("avg_error_results.csv"),
                kind=PathKind.FILE,
            ),
            self.path_check(
                name="ref_p99_error_results",
                path=self.ref_path("p99_error_results.csv"),
                kind=PathKind.FILE,
            ),
        ]

        for csv_rel in _TOY_CSVS:
            path = Path(csv_rel)
            reqs.append(
                NonEmptyFileCheck(
                    name=f"toy_{path.stem}",
                    path=self.workspace_path(path),
                )
            )

        for csv_rel in _workload2_csvs():
            path = Path(csv_rel)
            reqs.append(
                NonEmptyFileCheck(
                    name=f"wl2_{path.stem}",
                    path=self.workspace_path(path),
                )
            )

        for csv_rel in _GAVEL_CSVS:
            path = Path(csv_rel)
            reqs.append(
                NonEmptyFileCheck(
                    name=f"wl3_{path.stem}",
                    path=self.workspace_path(path),
                )
            )

        reqs.extend(
            (
                NonEmptyFileCheck(
                    name="timing_expt_pkl",
                    path=self.workspace_path("new_data", "timing_expt.pkl"),
                ),
                NonEmptyFileCheck(
                    name="size_error_expt_pkl",
                    path=self.workspace_path("new_data", "size_error_expt.pkl"),
                ),
                SimulationMetricCorrelationCheck(
                    name="avg_jct_correlation",
                    new_data_dir=new_data,
                    reference_path=self.ref_path("avg_jct_results.csv"),
                    traces=_WORKLOAD2_TRACES,
                    metric="avg_jct",
                    threshold=_JCT_SIMILARITY,
                    normalize_jct=True,
                ),
                SimulationMetricCorrelationCheck(
                    name="avg_error_correlation",
                    new_data_dir=new_data,
                    reference_path=self.ref_path("avg_error_results.csv"),
                    traces=_WORKLOAD2_TRACES,
                    metric="avg_error",
                    threshold=_ERROR_SIMILARITY,
                ),
                SimulationMetricCorrelationCheck(
                    name="p99_error_correlation",
                    new_data_dir=new_data,
                    reference_path=self.ref_path("p99_error_results.csv"),
                    traces=_WORKLOAD2_TRACES,
                    metric="p99_error",
                    threshold=_ERROR_SIMILARITY,
                ),
            )
        )

        return tuple(reqs)