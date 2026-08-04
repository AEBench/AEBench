from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles.reporting import BaseCheck, CheckResult
from evaluator.oracles.bases import CaseOracleExperimentRunsBase
from evaluator.oracles.checks import (
    ListSimilarityCheck,
    PathKind,
    SimilarityMetric,
)

_log = logging.getLogger(__name__)


def _extract_columns_from_csv(path: Path, target_columns: tuple[str, ...]) -> list[float]:
    """Generically parse a CSV and extract floats only from specified columns."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    reader = csv.DictReader(text.strip().splitlines())
    extracted: list[float] = []

    for row in reader:
        for col in target_columns:
            if col in row:
                val_str = row[col].strip()
                if not val_str or val_str in ("/", "-") or "over" in val_str:
                    continue
                try:
                    extracted.append(float(val_str))
                except ValueError:
                    pass

    return extracted


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
class CorrelationCheck(BaseCheck):
    """Compare specific columns from a generated CSV against a reference CSV."""
    generated_path: Path
    reference_path: Path
    target_columns: tuple[str, ...]
    threshold: float = 0.85

    def check(self) -> CheckResult:
        if not self.generated_path.is_file():
            return CheckResult.failure(f"Generated file missing: {self.generated_path}")
        if not self.reference_path.is_file():
            return CheckResult.failure(f"Reference file missing: {self.reference_path}")

        observed = _extract_columns_from_csv(self.generated_path, self.target_columns)
        reference = _extract_columns_from_csv(self.reference_path, self.target_columns)

        if not observed or not reference:
            return CheckResult.failure(
                f"Could not extract target columns {self.target_columns} from {self.generated_path.name} "
                f"(Observed: {len(observed)}, Reference: {len(reference)})"
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
            safe_name = artifact.replace("/", "_").replace(".", "_").replace("(", "").replace(")", "")
            reqs.append(
                NonEmptyFileCheck(
                    name=f"exists_{safe_name}",
                    path=examples_dir / artifact,
                )
            )


        csv_validation_targets = [
            ("table4-compare/overhead.csv", ("morph_confidence", "morph_gates_num")),
            ("fig7-quantumlock/quantumlock.csv", ("samples",)),
            ("fig15a-ablation_study/accuracy.csv", ("clliford", "basis_gate")),
            ("fig12-confidence/distribution_samples64.csv", ("confidence",)),
        ]

        for csv_rel_path, target_cols in csv_validation_targets:
            safe_name = csv_rel_path.replace("/", "_").replace(".csv", "").replace("-", "_")
            
            # Check file is not empty
            reqs.append(
                NonEmptyFileCheck(
                    name=f"not_empty_{safe_name}",
                    path=examples_dir / csv_rel_path,
                )
            )

            #  Compare with reference
            reqs.append(
                CorrelationCheck(
                    name=f"correlation_{safe_name}",
                    generated_path=examples_dir / csv_rel_path,
                    reference_path=self.ref_path(csv_rel_path),
                    target_columns=target_cols,
                )
            )

        return tuple(reqs)