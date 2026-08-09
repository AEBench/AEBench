from __future__ import annotations

import json
import re
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
    METRIC_KEYS,
    REQUIRED_SIM_LOGS,
    REQUIRED_WORKLOADS,
    RESULTS_REF,
    SIMILARITY_THRESHOLD,
    find_repo_root,
)

_METRIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "memory_system_cycles": re.compile(
        r"(?:^|\n)\s*memory_system_cycles:\s*([0-9]+)",
        re.MULTILINE,
    ),
    "total_num_read_requests": re.compile(
        r"(?:^|\n)\s*total_num_read_requests:\s*([0-9]+)",
        re.MULTILINE,
    ),
    "total_num_write_requests": re.compile(
        r"(?:^|\n)\s*total_num_write_requests:\s*([0-9]+)",
        re.MULTILINE,
    ),
    "s_total_mem_req": re.compile(
        r"(?:^|\n)\s*s_total_mem_req:\s*([0-9]+)",
        re.MULTILINE,
    ),
}

_ALLOWED_WORKLOADS = frozenset(REQUIRED_WORKLOADS)


def _parse_sim_log(path: Path) -> dict[str, int] | None:
    """Parse Ramulator2 summary metrics from a UniZK simulation .log file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    metrics: dict[str, int] = {}
    for key, pattern in _METRIC_PATTERNS.items():
        match = pattern.search(text)
        if match is None:
            return None
        metrics[key] = int(match.group(1))
    return metrics


def _load_reference(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("simulation metrics reference must be a JSON object")
    workloads = data.get("workloads")
    if not isinstance(workloads, dict) or not workloads:
        raise ValueError("simulation metrics reference missing workloads")
    return data


def _metric_sequence(
    workloads: Mapping[str, Any],
    *,
    metric: str,
    names: Sequence[str],
) -> list[float]:
    values: list[float] = []
    for name in names:
        entry = workloads[name]
        if not isinstance(entry, dict):
            raise ValueError(f"workload {name!r} must be an object")
        if metric not in entry:
            raise ValueError(f"workload {name!r} missing metric {metric!r}")
        values.append(float(entry[metric]))
    return values


@dataclass(frozen=True, slots=True, kw_only=True)
class SimulationLogsPresentCheck(BaseCheck):
    """Fail unless every required simulation log exists and parses."""

    repo_root: Path
    required_logs: tuple[str, ...]

    def check(self) -> CheckResult:
        missing: list[str] = []
        unparsable: list[str] = []

        for name in self.required_logs:
            path = self.repo_root / name
            if not path.is_file():
                missing.append(name)
                continue
            if _parse_sim_log(path) is None:
                unparsable.append(name)

        if missing or unparsable:
            parts: list[str] = []
            if missing:
                parts.append("missing: " + ", ".join(missing))
            if unparsable:
                parts.append(
                    "unparsable (need memory_system_cycles / read / write / "
                    "s_total_mem_req): " + ", ".join(unparsable)
                )
            return CheckResult.failure("; ".join(parts))

        return CheckResult.success(
            message=f"found and parsed {len(self.required_logs)} simulation log(s)"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceWorkloadsExactCheck(BaseCheck):
    """Fail unless the reference workloads match REQUIRED_WORKLOADS exactly."""

    reference_path: Path
    required_workloads: tuple[str, ...]

    def check(self) -> CheckResult:
        try:
            reference = _load_reference(self.reference_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return CheckResult.failure(f"cannot load reference: {exc}")

        workloads = reference["workloads"]
        if not isinstance(workloads, dict):
            return CheckResult.failure("reference workloads must be an object")

        observed = set(workloads)
        expected = set(self.required_workloads)
        disallowed = sorted(name for name in observed if name not in _ALLOWED_WORKLOADS)
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)

        if disallowed or missing or extra:
            parts: list[str] = []
            if disallowed:
                parts.append("disallowed workload names: " + ", ".join(disallowed))
            if missing:
                parts.append("missing required workloads: " + ", ".join(missing))
            if extra:
                parts.append("unexpected workloads: " + ", ".join(extra))
            return CheckResult.failure("; ".join(parts))

        return CheckResult.success(
            message=f"reference workloads match required set ({len(expected)})"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SimulationMetricSimilarityCheck(BaseCheck):
    """Pearson-correlate one metric across the fixed required workloads."""

    repo_root: Path
    reference_path: Path
    metric: str
    threshold: float
    required_workloads: tuple[str, ...]

    def check(self) -> CheckResult:
        try:
            reference = _load_reference(self.reference_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return CheckResult.failure(f"cannot load reference: {exc}")

        workloads = reference["workloads"]
        if not isinstance(workloads, dict):
            return CheckResult.failure("reference workloads must be an object")

        ordered = list(self.required_workloads)
        for name in ordered:
            if name not in _ALLOWED_WORKLOADS:
                return CheckResult.failure(f"disallowed workload name: {name!r}")
            if name not in workloads:
                return CheckResult.failure(f"reference missing workload {name!r}")

        observed_workloads: dict[str, dict[str, int]] = {}
        missing: list[str] = []
        for name in ordered:
            # Only allowlisted stems are used to build paths.
            metrics = _parse_sim_log(self.repo_root / f"{name}.log")
            if metrics is None:
                missing.append(f"{name}.log")
                continue
            observed_workloads[name] = metrics

        if missing:
            return CheckResult.failure(
                "missing or unparsable simulation logs: " + ", ".join(missing)
            )

        try:
            observed = _metric_sequence(
                observed_workloads,
                metric=self.metric,
                names=ordered,
            )
            expected = _metric_sequence(
                workloads,
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
                name="simulation_metrics_ref_exists",
                path=reference_path,
                kind=PathKind.FILE,
            ),
            ReferenceWorkloadsExactCheck(
                name="reference_workloads_exact",
                reference_path=reference_path,
                required_workloads=REQUIRED_WORKLOADS,
            ),
            SimulationLogsPresentCheck(
                name="required_simulation_logs_present",
                repo_root=repo_root,
                required_logs=REQUIRED_SIM_LOGS,
            ),
        ]

        for metric in METRIC_KEYS:
            checks.append(
                SimulationMetricSimilarityCheck(
                    name=f"simulation_{metric}_correlation",
                    repo_root=repo_root,
                    reference_path=reference_path,
                    metric=metric,
                    threshold=SIMILARITY_THRESHOLD,
                    required_workloads=REQUIRED_WORKLOADS,
                )
            )

        return tuple(checks)
