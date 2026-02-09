"""Experiment runs oracle for EGWALKER (EuroSys'25).

Validates:
  - Timing results file can be read and parsed.
  - Ground-truth reference timings file exists and can be read.
  - Observed timings meet the configured similarity threshold against reference timings.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluator import utils
from evaluator.oracle_experiment_runs_primitives import (
    ElementwiseSimilarityThresholdRequirement,
    ExperimentRunsContext,
    OracleExperimentRunsBase,
)
from evaluator.utils import EntryConfig


def _required_path(paths: Mapping[str, Path], key: str, *, label: str) -> Path:
    """Returns a required path from a mapping with a clear error message."""
    try:
        p = paths[key]
    except KeyError as exc:
        raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from exc
    return p


def _load_json_file(path: Path, *, label: str) -> object:
    """Loads JSON from a file path with consistent error messages."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{label}: failed to read {path}: {exc}") from exc
    text = text.strip()
    if not text:
        raise ValueError(f"{label}: empty JSON content at {path}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: invalid JSON in {path}: {exc}") from exc


def _as_float(v: object, *, label: str) -> float:
    """Converts numeric values to float; raises on non-numeric."""
    if isinstance(v, (int, float)):
        return float(v)
    raise ValueError(f"{label}: non-numeric value {v!r}")


def _iter_metric_tag_rows(
    obj: object, *, label: str
) -> Iterable[tuple[str, Mapping[str, object]]]:
    """Yields (row_key, stats_dict) where row_key is '<metric>.<tag>'."""
    if not isinstance(obj, dict):
        raise ValueError(f"{label}: timings JSON must be an object at top-level")

    for metric_name, metric in obj.items():
        if not isinstance(metric_name, str):
            raise ValueError(f"{label}: non-string metric name {metric_name!r}")
        if not isinstance(metric, dict):
            raise ValueError(f"{label}: {metric_name!r} must map to an object")

        for tag, stats in metric.items():
            if not isinstance(tag, str):
                raise ValueError(f"{label}: non-string tag name {tag!r}")
            if not isinstance(stats, dict):
                raise ValueError(f"{label}: {metric_name}.{tag} must map to an object")

            row_key = f"{metric_name}.{tag}"
            yield row_key, stats


def _discover_reference_fields(reference_obj: object, *, label: str) -> tuple[str, ...]:
    """Returns unique stats fields in order of first appearance in the reference."""
    seen: set[str] = set()
    ordered: list[str] = []
    for _row_key, stats in _iter_metric_tag_rows(reference_obj, label=label):
        for field in stats.keys():
            if not isinstance(field, str):
                raise ValueError(f"{label}: non-string field name {field!r}")
            if field not in seen:
                seen.add(field)
                ordered.append(field)
    return tuple(ordered)


def _values_by_label_for_field(
    obj: object,
    *,
    field: str | None,
    label: str,
) -> dict[str, float]:
    """Extracts timing values keyed by stable labels.

    If field is not None:
      - label is '<metric>.<tag>'
      - value is stats[field]
      - rows missing the field are skipped (so the *reference* defines expectation)

    If field is None (flatten):
      - label is '<metric>.<tag>.<field>'
      - value is stats[field]
    """
    out: dict[str, float] = {}
    for row_key, stats in _iter_metric_tag_rows(obj, label=label):
        if field is None:
            for f, raw in stats.items():
                if not isinstance(f, str):
                    raise ValueError(f"{label}: non-string field name {f!r}")
                k = f"{row_key}.{f}"
                if k in out:
                    raise ValueError(f"{label}: duplicate label {k!r}")
                out[k] = _as_float(raw, label=f"{label}: {k}")
        else:
            if field not in stats:
                continue
            if row_key in out:
                raise ValueError(f"{label}: duplicate label {row_key!r}")
            out[row_key] = _as_float(stats[field], label=f"{label}: {row_key}.{field}")
    return out


def _format_missing_labels(missing: Sequence[str], *, max_items: int = 10) -> str:
    if not missing:
        return ""
    head = list(missing[:max_items])
    more = len(missing) - len(head)
    suffix = f"\n... ({more} more)" if more > 0 else ""
    return "missing labels:\n" + "\n".join(f"- {k}" for k in head) + suffix


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TimingsJSONSimilarityRequirement(utils.BaseRequirement):
    """Artifact-specific wrapper that delegates numeric checks to base primitives."""

    results_path: Path
    reference_path: Path
    threshold: float
    field: str | None = None 
    abs_epsilon: float = 1e-12
    max_mismatches_to_report: int = 10

    def check(self, ctx: ExperimentRunsContext) -> utils.CheckResult:
        try:
            results_obj = _load_json_file(self.results_path, label="timings results")
            reference_obj = _load_json_file(self.reference_path, label="timings reference")

            ref_map = _values_by_label_for_field(
                reference_obj, field=self.field, label="timings reference"
            )
            res_map = _values_by_label_for_field(
                results_obj, field=self.field, label="timings results"
            )

            expected_labels = sorted(ref_map.keys())
            missing = [k for k in expected_labels if k not in res_map]
            if missing:
                detail = _format_missing_labels(missing, max_items=self.max_mismatches_to_report)
                msg = f"{self.name}: results missing required reference entries"
                if detail:
                    msg = f"{msg}\n{detail}"
                return utils.CheckResult.failure(msg)

            observed = [res_map[k] for k in expected_labels]
            reference = [ref_map[k] for k in expected_labels]
        except ValueError as exc:
            return utils.CheckResult.failure(f"{self.name}: {exc}")

        delegated = ElementwiseSimilarityThresholdRequirement(
            name=self.name,
            optional=self.optional,
            observed=observed,
            reference=reference,
            threshold=self.threshold,
            abs_epsilon=self.abs_epsilon,
            max_mismatches_to_report=self.max_mismatches_to_report,
        )
        return delegated.check(ctx)


class OracleExperimentRuns(OracleExperimentRunsBase):
    """Validates experiment run timings."""

    def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
        super().__init__(logger=logger)
        self._config = config

    def requirements(self) -> Sequence[utils.BaseRequirement]:
        if not self._config.results_paths:
            raise ValueError("EntryConfig.results_paths must be non-empty")
        if not self._config.ground_truth_paths:
            raise ValueError("EntryConfig.ground_truth_paths must be non-empty")

        results_path = _required_path(self._config.results_paths, "timings", label="results_paths")
        reference_path = _required_path(
            self._config.ground_truth_paths, "timings", label="ground_truth_paths"
        )

        threshold = self._config.similarity_ratio

        # Discover which fields to check from the reference.
        try:
            ref_obj = _load_json_file(reference_path, label="timings reference")
            fields = _discover_reference_fields(ref_obj, label="timings reference")
        except ValueError:
            fields = ()

        if not fields:
            # Fallback: compare all fields flattened.
            return (
                TimingsJSONSimilarityRequirement(
                    name="timings",
                    results_path=results_path,
                    reference_path=reference_path,
                    threshold=threshold,
                    field=None,
                ),
            )

        reqs: list[utils.BaseRequirement] = []
        for field in fields:
            reqs.append(
                TimingsJSONSimilarityRequirement(
                    name=f"timings_{field}",
                    results_path=results_path,
                    reference_path=reference_path,
                    threshold=threshold,
                    field=field,
                )
            )
        return tuple(reqs)
