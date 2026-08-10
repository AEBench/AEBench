from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import (
    ListSimilarityCheck,
    PathKind,
    SimilarityMetric,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

from .consts import (
    ACCURACY_CSV_GLOB,
    METRIC_KEYS,
    REQUIRED_RUNS,
    RESULTS_AGGREGATE,
    RESULTS_REF,
    RUN_KEY_BY_SIM_CLUSTER,
    SIMILARITY_THRESHOLD,
    find_repo_root,
)

_ALLOWED_RUNS = frozenset(REQUIRED_RUNS)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_run_metrics(entry: Mapping[str, Any]) -> dict[str, float] | None:
    metrics: dict[str, float] = {}
    for key in METRIC_KEYS:
        parsed = _as_float(entry.get(key))
        if parsed is None:
            return None
        metrics[key] = parsed
    return metrics


def _load_reference(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("accuracy metrics reference must be a JSON object")
    runs = data.get("runs")
    if not isinstance(runs, dict) or not runs:
        raise ValueError("accuracy metrics reference missing runs")
    return data


def _metric_sequence(
    runs: Mapping[str, Any],
    *,
    metric: str,
    names: Sequence[str],
) -> list[float]:
    values: list[float] = []
    for name in names:
        entry = runs[name]
        if not isinstance(entry, dict):
            raise ValueError(f"run {name!r} must be an object")
        if metric not in entry:
            raise ValueError(f"run {name!r} missing metric {metric!r}")
        values.append(float(entry[metric]))
    return values


def _complete_runs(
    parsed: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]] | None:
    """Accept a parsed map only when every allowlisted required run is present."""
    if any(name not in parsed for name in REQUIRED_RUNS):
        return None
    return {name: parsed[name] for name in REQUIRED_RUNS}


def _load_runs_from_aggregate(path: Path) -> dict[str, dict[str, float]] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_runs = data.get("runs", data)
    if not isinstance(raw_runs, dict):
        return None
    parsed: dict[str, dict[str, float]] = {}
    for name, entry in raw_runs.items():
        if name not in _ALLOWED_RUNS or not isinstance(entry, dict):
            continue
        metrics = _parse_run_metrics(entry)
        if metrics is not None:
            parsed[name] = metrics
    return _complete_runs(parsed)


def _load_runs_from_per_run_json(results_dir: Path) -> dict[str, dict[str, float]] | None:
    parsed: dict[str, dict[str, float]] = {}
    for name in REQUIRED_RUNS:
        path = results_dir / f"{name}.json"
        if not path.is_file():
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entry, dict):
            continue
        metrics = _parse_run_metrics(entry)
        if metrics is not None:
            parsed[name] = metrics
    return _complete_runs(parsed)


def _load_runs_from_accuracy_csv(results_dir: Path) -> dict[str, dict[str, float]] | None:
    candidates = [path for path in results_dir.glob(ACCURACY_CSV_GLOB) if path.is_file()]
    if not candidates:
        return None
    # Prefer the newest accuracy CSV from exp.py (mtime, not lexical timestamp).
    path = max(candidates, key=lambda candidate: candidate.stat().st_mtime)
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except OSError:
        return None

    parsed: dict[str, dict[str, float]] = {}
    for row in rows:
        simulator = str(row.get("simulator", "")).strip().lower()
        cluster = _as_float(row.get("cluster"))
        if cluster is None:
            continue
        key = RUN_KEY_BY_SIM_CLUSTER.get((simulator, int(cluster)))
        if key is None or key not in _ALLOWED_RUNS:
            continue
        metrics = _parse_run_metrics(row)
        if metrics is not None:
            parsed[key] = metrics
    return _complete_runs(parsed)


def _discover_observed_runs(repo_root: Path) -> dict[str, dict[str, float]] | None:
    """Load allowlisted run metrics from JSON aggregate, per-run JSON, or accuracy CSV."""
    results_dir = repo_root / "results"
    if not results_dir.is_dir():
        return None

    for loader in (
        lambda: _load_runs_from_aggregate(results_dir / RESULTS_AGGREGATE),
        lambda: _load_runs_from_per_run_json(results_dir),
        lambda: _load_runs_from_accuracy_csv(results_dir),
    ):
        loaded = loader()
        if loaded is not None:
            return loaded
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class AccuracyResultsPresentCheck(BaseCheck):
    """Fail unless every required accuracy-style run is present and parsable."""

    repo_root: Path
    required_runs: tuple[str, ...]

    def check(self) -> CheckResult:
        observed = _discover_observed_runs(self.repo_root)
        if observed is None:
            return CheckResult.failure(
                "missing accuracy results under results/ "
                f"(need {RESULTS_AGGREGATE}, per-run JSON, or {ACCURACY_CSV_GLOB})"
            )

        missing = [name for name in self.required_runs if name not in observed]
        if missing:
            return CheckResult.failure(
                "missing required accuracy runs: " + ", ".join(missing)
            )
        return CheckResult.success(
            message=f"found and parsed {len(self.required_runs)} accuracy run(s)"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceRunsExactCheck(BaseCheck):
    """Fail unless the reference runs match REQUIRED_RUNS exactly."""

    reference_path: Path
    required_runs: tuple[str, ...]

    def check(self) -> CheckResult:
        try:
            reference = _load_reference(self.reference_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return CheckResult.failure(f"cannot load reference: {exc}")

        runs = reference["runs"]
        if not isinstance(runs, dict):
            return CheckResult.failure("reference runs must be an object")

        observed = set(runs)
        expected = set(self.required_runs)
        disallowed = sorted(name for name in observed if name not in _ALLOWED_RUNS)
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)

        if disallowed or missing or extra:
            parts: list[str] = []
            if disallowed:
                parts.append("disallowed run names: " + ", ".join(disallowed))
            if missing:
                parts.append("missing required runs: " + ", ".join(missing))
            if extra:
                parts.append("unexpected runs: " + ", ".join(extra))
            return CheckResult.failure("; ".join(parts))

        return CheckResult.success(
            message=f"reference runs match required set ({len(expected)})"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AccuracyMetricSimilarityCheck(BaseCheck):
    """Pearson-correlate one metric across the fixed required accuracy runs."""

    repo_root: Path
    reference_path: Path
    metric: str
    threshold: float
    required_runs: tuple[str, ...]

    def check(self) -> CheckResult:
        try:
            reference = _load_reference(self.reference_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return CheckResult.failure(f"cannot load reference: {exc}")

        runs = reference["runs"]
        if not isinstance(runs, dict):
            return CheckResult.failure("reference runs must be an object")

        ordered = list(self.required_runs)
        for name in ordered:
            if name not in _ALLOWED_RUNS:
                return CheckResult.failure(f"disallowed run name: {name!r}")
            if name not in runs:
                return CheckResult.failure(f"reference missing run {name!r}")

        observed_runs = _discover_observed_runs(self.repo_root)
        if observed_runs is None:
            return CheckResult.failure("missing or unparsable accuracy results")

        missing = [name for name in ordered if name not in observed_runs]
        if missing:
            return CheckResult.failure(
                "missing or unparsable accuracy runs: " + ", ".join(missing)
            )

        try:
            observed = _metric_sequence(
                observed_runs,
                metric=self.metric,
                names=ordered,
            )
            expected = _metric_sequence(
                runs,
                metric=self.metric,
                names=ordered,
            )
        except ValueError as exc:
            return CheckResult.failure(str(exc))

        if len(observed) < 2:
            return CheckResult.failure(
                f"need at least 2 values for {self.metric} correlation, "
                f"found {len(observed)}"
            )

        return ListSimilarityCheck(
            name=self.name,
            optional=self.optional,
            observed=observed,
            reference=expected,
            metric=SimilarityMetric.PEARSON,
            min_similarity=self.threshold,
        ).check()


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        repo_root = find_repo_root(self.workspace_path())
        reference_path = self.ref_path(RESULTS_REF)

        checks: list[BaseCheck] = [
            self.path_check(
                name="accuracy_metrics_ref_exists",
                path=reference_path,
                kind=PathKind.FILE,
            ),
            ReferenceRunsExactCheck(
                name="reference_runs_exact",
                reference_path=reference_path,
                required_runs=REQUIRED_RUNS,
            ),
            AccuracyResultsPresentCheck(
                name="required_accuracy_runs_present",
                repo_root=repo_root,
                required_runs=REQUIRED_RUNS,
            ),
        ]

        for metric in METRIC_KEYS:
            checks.append(
                AccuracyMetricSimilarityCheck(
                    name=f"accuracy_{metric}_correlation",
                    repo_root=repo_root,
                    reference_path=reference_path,
                    metric=metric,
                    threshold=SIMILARITY_THRESHOLD,
                    required_runs=REQUIRED_RUNS,
                )
            )

        return tuple(checks)
