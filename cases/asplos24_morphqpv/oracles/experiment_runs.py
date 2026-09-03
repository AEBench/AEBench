from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import (
    ElementwiseSimilarityThresholdCheck,
    ListSimilarityCheck,
    PathKind,
    SimilarityMetric,
)
from evaluator.oracles.reporting import BaseCheck, CheckResult

_NAME_SAFE_TRANSLATION = str.maketrans({"/": "_", ".": "_", "-": "_", "(": "", ")": ""})


def _safe_name(rel_path: str) -> str:
    """Build a check-name fragment from a workspace-relative path."""
    return rel_path.translate(_NAME_SAFE_TRANSLATION)


def _extract_column(
    path: Path,
    identity_columns: tuple[str, ...],
    target_column: str,
) -> dict[tuple[str, ...], float]:
    """Parse one numeric column from a CSV, keyed by the identity of its row. Raise if the file cannot be read or a row is truncated. """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"{path.name}: cannot read: {exc}") from exc

    reader = csv.DictReader(text.strip().splitlines())


    # Counting occurrences keeps repeat trials paired in file order.
    occurrences: Counter[tuple[str, ...]] = Counter()
    values: dict[tuple[str, ...], float] = {}

    for line_no, row in enumerate(reader, start=2):
        if target_column not in row:
            continue

        identity = tuple((row.get(col) or "").strip() for col in identity_columns)
        occurrences[identity] += 1
        key = (*identity, str(occurrences[identity]))


        raw = row[target_column]
        if raw is None:
            raise ValueError(
                f"{path.name}: truncated row {line_no} (no value for {target_column!r})"
            )

        try:
            values[key] = float(raw)
        except ValueError:
            continue

    return values


def _load_pair(
    generated_path: Path,
    reference_path: Path,
    identity_columns: tuple[str, ...],
    target_column: str,
) -> tuple[list[float], list[float]]:
    """Load one column from both files and align it on the rows they share. Raises if either file cannot be parsed."""
    observed = _extract_column(generated_path, identity_columns, target_column)
    reference = _extract_column(reference_path, identity_columns, target_column)

    keys = [key for key in reference if key in observed]
    return [observed[key] for key in keys], [reference[key] for key in keys]


@dataclass(frozen=True, slots=True, kw_only=True)
class NonEmptyFileCheck(BaseCheck):
    """Passes only if the file exists and has a size > 0 bytes."""

    path: Path

    def check(self) -> CheckResult:
        if not self.path.is_file():
            return CheckResult.failure(f"file missing: {self.path}")

        try:
            size = self.path.stat().st_size
        except OSError as exc:
            return CheckResult.failure(f"cannot stat {self.path}: {exc}")

        if size == 0:
            return CheckResult.failure(f"file is empty: {self.path}")

        return CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class ToleranceCheck(BaseCheck):
    """Require a column within a relative band, for determined values."""

    generated_path: Path
    reference_path: Path
    identity_columns: tuple[str, ...]
    target_column: str
    threshold: float

    def check(self) -> CheckResult:
        try:
            observed, reference = _load_pair(
                self.generated_path,
                self.reference_path,
                self.identity_columns,
                self.target_column,
            )
        except ValueError as exc:
            return CheckResult.failure(str(exc))

        return ElementwiseSimilarityThresholdCheck(
            name=self.name,
            optional=self.optional,
            observed=observed,
            reference=reference,
            threshold=self.threshold,
        ).check()


@dataclass(frozen=True, slots=True, kw_only=True)
class CorrelationCheck(BaseCheck):
    """Require a column to follow the reference trend, for sampled values."""

    generated_path: Path
    reference_path: Path
    identity_columns: tuple[str, ...]
    target_column: str
    threshold: float

    def check(self) -> CheckResult:
        try:
            observed, reference = _load_pair(
                self.generated_path,
                self.reference_path,
                self.identity_columns,
                self.target_column,
            )
        except ValueError as exc:
            return CheckResult.failure(str(exc))

        return ListSimilarityCheck(
            name=self.name,
            optional=self.optional,
            observed=observed,
            reference=reference,
            metric=SimilarityMetric.PEARSON,
            min_similarity=self.threshold,
        ).check()


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[BaseCheck]:
        examples_dir = self.workspace_path("examples")
        reqs: list[BaseCheck] = [
            self.path_check(
                name="examples_dir",
                path=examples_dir,
                kind=PathKind.DIRECTORY,
            )
        ]

        visual_artifacts = [
            "fig7-quantumlock/quantumlock.svg",
            "fig11a-theorem1/fig9(a).svg",
            "fig11b-theorem2/fig9(b)-theorem2.svg",
            "fig11-opt_strategy/optimize.pdf",
            "fig12-confidence/confidence.svg",
            "fig12(b)-solvers_compare/runtime.svg",
            "fig15a-ablation_study/fig12(a)-ablation_study.pdf",
        ]

        for artifact in visual_artifacts:
            reqs.append(
                NonEmptyFileCheck(
                    name=f"exists_{_safe_name(artifact)}",
                    path=examples_dir / artifact,
                )
            )

        # Columns that identify a row in each result file
        identity_columns = {
            "table4-compare/overhead.csv": ("name", "n_qubits"),
            "fig7-quantumlock/quantumlock.csv": ("n_qubits",),
            "fig15a-ablation_study/accuracy.csv": ("name", "qubits", "samples"),
            "fig12-confidence/distribution_samples64.csv": ("algo", "samples"),
        }

        for csv_rel_path in identity_columns:
            # Check file is not empty
            reqs.append(
                NonEmptyFileCheck(
                    name=f"not_empty_{_safe_name(csv_rel_path)}",
                    path=examples_dir / csv_rel_path,
                )
            )

        overhead = "table4-compare/overhead.csv"
        quantumlock = "fig7-quantumlock/quantumlock.csv"
        ablation = "fig15a-ablation_study/accuracy.csv"
        confidence = "fig12-confidence/distribution_samples64.csv"

        # The check follows what the paper asserts about each quantity (either trend or direct comparison)
        csv_validation_targets = [
            # 1.0 is exact equality: the paper claims exactly 100% confidence for morph_confidence
            (overhead, "morph_confidence", ToleranceCheck, 1.00),
            (overhead, "morph_gates_num", CorrelationCheck, 0.70),
            (quantumlock, "bases", ToleranceCheck, 0.95),
            (ablation, "clliford", CorrelationCheck, 0.70),
            (ablation, "basis_gate", CorrelationCheck, 0.70),
            (confidence, "mean", CorrelationCheck, 0.70),
        ]

        for csv_rel_path, target_col, check_cls, threshold in csv_validation_targets:
            #  Compare with reference
            reqs.append(
                check_cls(
                    name=f"{_safe_name(csv_rel_path)}_{target_col}",
                    generated_path=examples_dir / csv_rel_path,
                    reference_path=self.ref_path(csv_rel_path),
                    identity_columns=identity_columns[csv_rel_path],
                    target_column=target_col,
                    threshold=threshold,
                )
            )

        return tuple(reqs)
