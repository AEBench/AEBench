from __future__ import annotations

import json
import re
from collections.abc import Sequence
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

from .common import (
    METRIC_KEYS,
    REQUIRED_SIM_LOGS,
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


def _flatten_metrics(workloads: dict[str, Any], *, keys: Sequence[str]) -> list[float]:
    values: list[float] = []
    for name in sorted(workloads):
        entry = workloads[name]
        if not isinstance(entry, dict):
            raise ValueError(f"workload {name!r} must be an object")
        for key in keys:
            if key not in entry:
                raise ValueError(f"workload {name!r} missing metric {key!r}")
            values.append(float(entry[key]))
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
class SimulationMetricsSimilarityCheck(BaseCheck):
    """Pearson-correlate observed RamSim metrics against the reference."""

    repo_root: Path
    reference_path: Path
    threshold: float

    def check(self) -> CheckResult:
        try:
            reference = _load_reference(self.reference_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return CheckResult.failure(f"cannot load reference: {exc}")

        workloads = reference["workloads"]
        assert isinstance(workloads, dict)

        observed_workloads: dict[str, dict[str, int]] = {}
        missing: list[str] = []
        for name in sorted(workloads):
            log_name = f"{name}.log"
            metrics = _parse_sim_log(self.repo_root / log_name)
            if metrics is None:
                missing.append(log_name)
                continue
            observed_workloads[name] = metrics

        if missing:
            return CheckResult.failure(
                "missing or unparsable simulation logs for reference workloads: "
                + ", ".join(missing)
            )

        try:
            observed = _flatten_metrics(observed_workloads, keys=METRIC_KEYS)
            expected = _flatten_metrics(workloads, keys=METRIC_KEYS)
        except ValueError as exc:
            return CheckResult.failure(str(exc))

        if len(observed) < 2:
            return CheckResult.failure(
                f"need at least 2 metric values for correlation, found {len(observed)}"
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

        required_logs = list(REQUIRED_SIM_LOGS)
        try:
            reference = _load_reference(reference_path)
            workloads = reference.get("workloads")
            if isinstance(workloads, dict) and workloads:
                required_logs = [f"{name}.log" for name in sorted(workloads)]
        except (OSError, json.JSONDecodeError, ValueError):
            pass

        checks: list[BaseCheck] = [
            self.path_check(
                name="simulation_metrics_ref_exists",
                path=reference_path,
                kind=PathKind.FILE,
            ),
            SimulationLogsPresentCheck(
                name="required_simulation_logs_present",
                repo_root=repo_root,
                required_logs=tuple(required_logs),
            ),
            SimulationMetricsSimilarityCheck(
                name="simulation_metrics_correlation",
                repo_root=repo_root,
                reference_path=reference_path,
                threshold=SIMILARITY_THRESHOLD,
            ),
        ]

        return tuple(checks)
