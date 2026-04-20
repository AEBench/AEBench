from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType

_MIN_DETECTION_RATE = 90.0
_EXPECTED_GO_INSTRUCTIONS = 121

_RESULT_FILE_SEARCH_DIRS = ("", "tester")


def _find_result_file(repo_root: Path, filename: str) -> Path | None:
    """Find a result file in the repo root or tester/ subdirectory."""
    for subdir in _RESULT_FILE_SEARCH_DIRS:
        candidate = repo_root / subdir / filename if subdir else repo_root / filename
        if candidate.is_file():
            return candidate
    return None


def _parse_aggregated_total(results_text: str) -> float | None:
    """Return the Aggregated/Total detection rate, or None."""
    for line in results_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Aggregated"):
            percentages = re.findall(r"(\d+(?:\.\d+)?)%", stripped)
            if percentages:
                return float(percentages[-1])
    return None


def _count_total_go_instructions(results_text: str) -> int | None:
    """Count benchmark rows plus the Remaining line."""
    in_aggregated = False
    row_count = 0
    remaining_count = 0

    for line in results_text.splitlines():
        stripped = line.strip()

        if stripped.startswith("Benchmark\t"):
            in_aggregated = True
            continue

        if not in_aggregated:
            continue

        if stripped.startswith("Aggregated"):
            break

        remaining_match = re.match(r"Remaining\s+(\d+)\s+go\s+instruction", stripped)
        if remaining_match:
            remaining_count = int(remaining_match.group(1))
            continue

        if "\t" in stripped and stripped:
            row_count += 1

    if not in_aggregated:
        return None
    return row_count + remaining_count


def _has_cgo_examples_entries(results_text: str) -> bool:
    """True if the aggregated report contains cgo-examples entries."""
    in_aggregated = False
    for line in results_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Benchmark\t"):
            in_aggregated = True
            continue
        if not in_aggregated:
            continue
        if stripped.startswith("Aggregated"):
            break
        if "cgo-examples" in stripped:
            return True
    return False


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregatedDetectionRateCheck(utils.BaseCheck):
    """Fail if the aggregated detection rate is below the threshold."""

    results_path: Path
    min_rate: float

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
        try:
            text = self.results_path.read_text(encoding="utf-8")
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to read results file {self.results_path}: {exc}"
            )

        rate = _parse_aggregated_total(text)
        if rate is None:
            return utils.CheckResult.failure(
                "could not parse Aggregated/Total detection rate from results file"
            )

        if rate < self.min_rate:
            return utils.CheckResult.failure(
                f"aggregated detection rate {rate:.2f}% is below minimum {self.min_rate:.2f}%"
            )

        return utils.CheckResult.success(
            message=f"aggregated detection rate: {rate:.2f}%"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NoCgoExamplesCheck(utils.BaseCheck):
    """Fail if the aggregated report lists cgo-examples entries."""

    results_path: Path

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
        try:
            text = self.results_path.read_text(encoding="utf-8")
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to read results file {self.results_path}: {exc}"
            )

        if _has_cgo_examples_entries(text):
            return utils.CheckResult.failure(
                "aggregated report contains cgo-examples entries (expected only goker entries)"
            )

        return utils.CheckResult.success()


@dataclass(frozen=True, slots=True, kw_only=True)
class TotalGoInstructionsCheck(utils.BaseCheck):
    """Fail if the total go instruction count does not match expected."""

    results_path: Path
    expected_count: int

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
        try:
            text = self.results_path.read_text(encoding="utf-8")
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to read results file {self.results_path}: {exc}"
            )

        count = _count_total_go_instructions(text)
        if count is None:
            return utils.CheckResult.failure(
                "could not parse go instruction count from results file"
            )

        if count != self.expected_count:
            return utils.CheckResult.failure(
                f"total go instructions {count} != expected {self.expected_count}"
            )

        return utils.CheckResult.success(
            message=f"total go instructions: {count}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PerfCSVStructureCheck(utils.BaseCheck):
    """Fail if the performance CSV is missing expected columns."""

    csv_path: Path

    _EXPECTED_COLUMNS = (
        "Target",
        "GC cycles",
        "Mark clock OFF",
        "Mark clock ON",
        "CPU utilization OFF",
        "CPU utilization ON",
    )

    def check(self, *_args, **_kwargs) -> utils.CheckResult:
        try:
            text = self.csv_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return utils.CheckResult.failure(
                f"failed to read CSV file {self.csv_path}: {exc}"
            )

        if not text:
            return utils.CheckResult.failure("CSV file is empty")

        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as exc:
            return utils.CheckResult.failure(f"CSV parse error: {exc}")

        if len(rows) < 2:
            return utils.CheckResult.failure(
                f"CSV has {len(rows)} row(s), expected at least 2 (header + data)"
            )

        header_normalized = []
        for col in rows[0]:
            normalized = col.strip().split("(")[0].strip().replace("\u03bc", "u")
            header_normalized.append(normalized)

        missing = [
            expected for expected in self._EXPECTED_COLUMNS
            if not any(expected.lower() in h.lower() for h in header_normalized)
        ]

        if missing:
            return utils.CheckResult.failure(
                f"CSV missing expected columns: {missing}; found: {rows[0]}"
            )

        return utils.CheckResult.success(
            message=f"CSV has {len(rows) - 1} data rows with expected columns"
        )


class OracleExperimentRuns(CaseOracleExperimentRunsBase):

    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = self.paths.workspace_dir

        results_path = _find_result_file(repo_root, "results")
        perf_csv_path = _find_result_file(repo_root, "results-perf.csv")
        tex_path = _find_result_file(repo_root, "results.tex")

        checks: list[utils.BaseCheck] = []

        if results_path is not None:
            checks.extend([
                AggregatedDetectionRateCheck(
                    name="rq1a_aggregated_detection_rate",
                    results_path=results_path,
                    min_rate=_MIN_DETECTION_RATE,
                ),
                NoCgoExamplesCheck(
                    name="rq1a_no_cgo_examples",
                    results_path=results_path,
                ),
                TotalGoInstructionsCheck(
                    name="rq1a_total_go_instructions",
                    results_path=results_path,
                    expected_count=_EXPECTED_GO_INSTRUCTIONS,
                ),
            ])
        else:
            checks.append(
                FilesystemPathCheck(
                    name="results_file_exists",
                    path=repo_root / "results",
                    path_type=PathType.FILE,
                )
            )

        if perf_csv_path is not None:
            checks.append(
                PerfCSVStructureCheck(
                    name="rq2_perf_csv_structure",
                    csv_path=perf_csv_path,
                )
            )
        else:
            checks.append(
                FilesystemPathCheck(
                    name="results_perf_csv_exists",
                    path=repo_root / "results-perf.csv",
                    path_type=PathType.FILE,
                )
            )

        tex_fallback = repo_root / "results.tex"
        checks.append(
            FilesystemPathCheck(
                name="rq2_boxplot_tex_exists",
                path=tex_path or tex_fallback,
                path_type=PathType.FILE,
            )
        )

        return tuple(checks)
