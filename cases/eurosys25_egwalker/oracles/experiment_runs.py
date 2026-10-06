from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import CaseOracleExperimentRunsBase, ListSimilarityCheck, SimilarityMetric
from evaluator.oracles.oracle_checks_runtime import RuntimeCheckExecutor
from evaluator.oracles.reporting import BaseCheck, CheckResult

_SIMILARITY_RATIO = 0.75


def _as_float(value: object, *, label: str) -> float:
    if isinstance(value, int | float):
        return float(value)
    raise ValueError(f"{label}: non-numeric value {value!r}")


def _timing_rows(obj: object, *, label: str) -> Iterable[tuple[str, Mapping[str, object]]]:
    if not isinstance(obj, dict):
        raise ValueError(f"{label}: top-level JSON value must be an object")

    for metric_name, metric in obj.items():
        if not isinstance(metric_name, str) or not isinstance(metric, dict):
            raise ValueError(f"{label}: invalid metric entry {metric_name!r}")

        for tag, stats in metric.items():
            if not isinstance(tag, str) or not isinstance(stats, dict):
                raise ValueError(f"{label}: invalid tag entry under {metric_name!r}")
            yield f"{metric_name}.{tag}", stats


def _fields(reference: object) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []

    for _row, stats in _timing_rows(reference, label="timings reference"):
        for field in stats:
            if not isinstance(field, str):
                raise ValueError("timings reference: field names must be strings")
            if field not in seen:
                seen.add(field)
                out.append(field)

    return tuple(out)


def _values(obj: object, *, field: str | None, label: str) -> dict[str, float]:
    out: dict[str, float] = {}

    for row, stats in _timing_rows(obj, label=label):
        if field is None:
            for name, value in stats.items():
                if not isinstance(name, str):
                    raise ValueError(f"{label}: field names must be strings")
                out[f"{row}.{name}"] = _as_float(value, label=f"{label}: {row}.{name}")
            continue

        if field in stats:
            out[row] = _as_float(stats[field], label=f"{label}: {row}.{field}")

    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class TimingsJSONSimilarityCheck(BaseCheck):
    results_path: Path
    reference_path: Path
    threshold: float
    field: str | None = None

    def check(self, executor: RuntimeCheckExecutor) -> CheckResult:
        try:
            results = json.loads(executor.read_file_text(self.results_path))
            reference = json.loads(executor.read_file_text(self.reference_path))

            ref_values = _values(reference, field=self.field, label="timings reference")
            got_values = _values(results, field=self.field, label="timings results")

            labels = sorted(ref_values)
            missing = [label for label in labels if label not in got_values]
            if missing:
                shown = "\n".join(f"- {label}" for label in missing[:10])
                return CheckResult.failure(
                    f"{self.name}: results missing required timing entries\n{shown}"
                )

            observed = [got_values[label] for label in labels]
            expected = [ref_values[label] for label in labels]
        except Exception as exc:
            return CheckResult.failure(f"{self.name}: {exc}")

        return ListSimilarityCheck(
            name=self.name,
            optional=self.optional,
            observed=observed,
            reference=expected,
            metric=SimilarityMetric.PEARSON,
            min_similarity=self.threshold,
        ).check(executor)


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        reference_path = self.ref_path("timings.ref.json")
        results_path = self.workspace_path("results", "timings.json")

        try:
            reference = json.loads(self.read_text(reference_path))
            fields = _fields(reference)
        except Exception:
            fields = ()

        if not fields:
            return (
                TimingsJSONSimilarityCheck(
                    name="timings",
                    results_path=results_path,
                    reference_path=reference_path,
                    threshold=_SIMILARITY_RATIO,
                ),
            )

        return tuple(
            TimingsJSONSimilarityCheck(
                name=f"timings_{field}",
                results_path=results_path,
                reference_path=reference_path,
                threshold=_SIMILARITY_RATIO,
                field=field,
            )
            for field in fields
        )
