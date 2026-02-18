"""Experiment runs oracle for DepSurf (EuroSys'25).

Validates:
 - All expected DepSurf CSV result tables can be read and parsed
 - Ground-truth reference CSV tables exist and can be read
 - Observed tables meet the configured similarity threshold against reference tables
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from evaluator import utils
from evaluator.oracle_experiment_runs_primitives import (
    ExperimentRunsContext,
    ListSimilarityRequirement,
    OracleExperimentRunsBase,
    SimilarityMetric,
)
from evaluator.utils import EntryConfig


def _required_path(paths: Mapping[str, Path], key: str, *, label: str) -> Path:
  """Returns a required path from a mapping with a clear error message."""
  try:
    p = paths[key]
  except KeyError as exc:
    raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from exc
  return p


def _normalize_cell(text: str) -> str:
  """Normalizes a CSV cell string for numeric parsing by stripping whitespaces 
  and treating "missing" symbols as empty cells.
  """
  s = text.strip()
  if not s:
    return ""
  if s in {"-", "—", "NA", "N/A", "nan", "NaN"}:
    return ""
  return s


def _as_float_or_zero(text: str, *, label: str) -> float:
  """Parses a numeric CSV cell with empty translated to 0.0, or raises error 
  on non-numeric entries.
  """
  s = _normalize_cell(text)
  if not s:
    return 0.0
  try:
    v = float(s)
  except ValueError as exc:
    raise ValueError(f"{label}: non-numeric cell {text!r}") from exc
  if not math.isfinite(v):
    raise ValueError(f"{label}: non-finite numeric cell {text!r}")
  return v


def _load_csv_matrix(
    path: Path,
    *,
    label: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, ...], ...]]:
  """Loads a CSV file as (headers, row_keys, rows)."""
  try:
    with path.open("r", encoding="utf-8", newline="") as f:
      reader = csv.reader(f)
      raw = list(reader)
  except OSError as exc:
    raise ValueError(f"{label}: failed to read {path}: {exc}") from exc

  # Drop leading/trailing blank lines
  raw = [r for r in raw if any(c.strip() for c in r)]
  if not raw:
    raise ValueError(f"{label}: empty CSV at {path}")

  headers = tuple(raw[0])
  if len(headers) < 2:
    raise ValueError(
        f"{label}: expected >=2 columns, got {len(headers)} in {path}")

  out_rows: list[tuple[str, ...]] = []
  row_keys: list[str] = []
  seen: set[str] = set()

  for i, r in enumerate(raw[1:], start=2):
    # Pad short rows; ignore extra columns beyond header length
    padded = list(r[:len(headers)]) + [""] * max(0, len(headers) - len(r))
    key = padded[0].strip()
    if not key:
      raise ValueError(f"{label}: empty row key at line {i} in {path}")
    if key in seen:
      raise ValueError(f"{label}: duplicate row key {key!r} in {path}")
    seen.add(key)
    row_keys.append(key)
    out_rows.append(tuple(padded))

  return headers, tuple(row_keys), tuple(out_rows)


def _discover_numeric_columns(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    label: str,
) -> tuple[str, ...]:
  """Returns numeric column header names in order, excluding column 0,"""
  numeric: list[str] = []
  for k, header in enumerate(headers):
    if k == 0:
      continue

    saw_value = False
    for i, row in enumerate(rows):
      if k >= len(row):
        continue
      cell = _normalize_cell(row[k])
      if not cell:
        continue
      saw_value = True
      try:
        v = float(cell)
      except ValueError as exc:
        raise ValueError(
            f"{label}: column {header!r} has non-numeric cell {row[k]!r} at row {i}"
        ) from exc
      if not math.isfinite(v):
        raise ValueError(
            f"{label}: column {header!r} has non-finite cell {row[k]!r} at row {i}"
        )
    if saw_value:
      numeric.append(header)
  if not numeric:
    raise ValueError(f"{label}: could not discover any numeric columns")
  return tuple(numeric)


def _format_missing(items: Sequence[str], *, max_items: int = 10) -> str:
  if not items:
    return ""
  head = list(items[:max_items])
  more = len(items) - len(head)
  suffix = f"\n... ({more} more)" if more > 0 else ""
  return "\n".join(f"- {k}" for k in head) + suffix


def _column_totals(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    numeric_cols: Sequence[str],
    label: str,
    max_items: int,
) -> list[float]:
  """Computes per-column "totals"."""
  header_to_idx = {h: i for i, h in enumerate(headers)}
  missing_cols = [h for h in numeric_cols if h not in header_to_idx]
  if missing_cols:
    detail = _format_missing(missing_cols, max_items=max_items)
    msg = f"{label}: missing required numeric columns"
    if detail:
      msg = f"{msg}\nmissing columns:\n{detail}"
    raise ValueError(msg)

  totals: list[float] = []
  for col_name in numeric_cols:
    j = header_to_idx[col_name]
    s = 0.0
    for i, r in enumerate(rows):
      cell = r[j] if j < len(r) else ""
      s += _as_float_or_zero(cell, label=f"{label}: row[{i}].{col_name}")
    totals.append(s)
  return totals


def _fractions_from_totals(totals: Sequence[float]) -> list[float]:
  """Normalizes totals into fractions."""
  den = sum(totals)
  if den <= 0.0:
    return [0.0 for _ in totals]
  return [v / den for v in totals]


@enum.unique
class _VectorType(enum.Enum):
  """Which aggregated vector to compare for a table under Mode B."""

  TOTALS = "totals"
  FRACTIONS = "fractions"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CSVTableModeBPearsonRequirement(utils.BaseRequirement):
  """Pearson similarity on column totals or column fractions to check 
  for the same relative number patterns and distribution/shape.
  
  Attributes:
    results_path: Path to the CSV produced by the current run.
    reference_path: Path to the reference CSV shipped with the evaluation bundle.
    threshold: Minimum acceptable Pearson correlation score.
    vector_type: Selects whether to compare raw totals or normalized fractions.
    max_mismatches_to_report: Maximum number of missing columns and similar issues to include in error messages.
  """

  results_path: Path
  reference_path: Path
  threshold: float
  vector_type: _VectorType
  max_mismatches_to_report: int = 10

  def check(self, ctx: ExperimentRunsContext) -> utils.CheckResult:
    try:
      ref_headers, _ref_row_keys, ref_rows = _load_csv_matrix(
          self.reference_path, label=f"{self.name} reference")
      res_headers, _res_row_keys, res_rows = _load_csv_matrix(
          self.results_path, label=f"{self.name} results")

      ref_numeric_cols = _discover_numeric_columns(
          ref_headers, ref_rows, label=f"{self.name} reference")

      reference_totals = _column_totals(
          ref_headers,
          ref_rows,
          numeric_cols=ref_numeric_cols,
          label=f"{self.name} reference",
          max_items=self.max_mismatches_to_report,
      )
      observed_totals = _column_totals(
          res_headers,
          res_rows,
          numeric_cols=ref_numeric_cols,
          label=f"{self.name} results",
          max_items=self.max_mismatches_to_report,
      )

      if self.vector_type == _VectorType.TOTALS:
        reference = reference_totals
        observed = observed_totals
      elif self.vector_type == _VectorType.FRACTIONS:
        reference = _fractions_from_totals(reference_totals)
        observed = _fractions_from_totals(observed_totals)
      else:
        raise ValueError(f"unsupported vector_type: {self.vector_type!r}")
    except ValueError as exc:
      return utils.CheckResult.failure(f"{self.name}: {exc}")

    delegated = ListSimilarityRequirement(
        name=self.name,
        optional=self.optional,
        observed=observed,
        reference=reference,
        metric=SimilarityMetric.PEARSON,
        min_similarity=self.threshold,
    )
    return delegated.check(ctx)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CSVTableModeBPearsonByRowRequirement(utils.BaseRequirement):
  """Compute Pearson similarity on a single numeric column aligned by row key.
  
  Attributes
    results_path: Path to the CSV produced by the current run.
    reference_path: Path to the reference CSV shipped with the evaluation bundle.
    threshold: Minimum acceptable Pearson correlation score.
    column_name: Name of the numeric column to extract and compare.
    vector_type: Selects whether to compare raw per-row values or normalized per-row fractions.
    max_mismatches_to_report: Maximum number of missing rows/columns to include in error messages.
  """

  results_path: Path
  reference_path: Path
  threshold: float
  column_name: str
  vector_type: _VectorType
  max_mismatches_to_report: int = 10

  def check(self, ctx: ExperimentRunsContext) -> utils.CheckResult:
    try:
      ref_headers, ref_row_keys, ref_rows = _load_csv_matrix(
          self.reference_path, label=f"{self.name} reference")
      res_headers, res_row_keys, res_rows = _load_csv_matrix(
          self.results_path, label=f"{self.name} results")

      ref_numeric_cols = _discover_numeric_columns(
          ref_headers, ref_rows, label=f"{self.name} reference")
      if self.column_name not in ref_numeric_cols:
        raise ValueError(
            f"reference does not contain required numeric column {self.column_name!r}"
        )

      res_header_to_idx = {h: i for i, h in enumerate(res_headers)}
      if self.column_name not in res_header_to_idx:
        raise ValueError(
            f"results missing required numeric column {self.column_name!r}")

      res_key_to_row: dict[str, Sequence[str]] = {
          k: r for k, r in zip(res_row_keys, res_rows, strict=True)
      }
      missing_rows = [k for k in ref_row_keys if k not in res_key_to_row]
      if missing_rows:
        detail = _format_missing(missing_rows,
                                 max_items=self.max_mismatches_to_report)
        msg = f"results missing required reference rows"
        if detail:
          msg = f"{msg}\nmissing rows:\n{detail}"
        raise ValueError(msg)

      ref_header_to_idx = {h: i for i, h in enumerate(ref_headers)}
      ref_j = ref_header_to_idx[self.column_name]
      res_j = res_header_to_idx[self.column_name]

      reference: list[float] = []
      observed: list[float] = []
      for row_key, ref_row in zip(ref_row_keys, ref_rows, strict=True):
        res_row = res_key_to_row[row_key]

        reference.append(
            _as_float_or_zero(
                ref_row[ref_j] if ref_j < len(ref_row) else "",
                label=f"{self.name} reference: {row_key}.{self.column_name}",
            ))
        observed.append(
            _as_float_or_zero(
                res_row[res_j] if res_j < len(res_row) else "",
                label=f"{self.name} results: {row_key}.{self.column_name}",
            ))

      if self.vector_type == _VectorType.FRACTIONS:
        reference = _fractions_from_totals(reference)
        observed = _fractions_from_totals(observed)
      elif self.vector_type != _VectorType.TOTALS:
        raise ValueError(f"unsupported vector_type: {self.vector_type!r}")
    except ValueError as exc:
      return utils.CheckResult.failure(f"{self.name}: {exc}")

    delegated = ListSimilarityRequirement(
        name=self.name,
        optional=self.optional,
        observed=observed,
        reference=reference,
        metric=SimilarityMetric.PEARSON,
        min_similarity=self.threshold,
    )
    return delegated.check(ctx)


class OracleExperimentRuns(OracleExperimentRunsBase):
  """Validates DepSurf experiment run result tables."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    if not self._config.results_paths:
      raise ValueError("EntryConfig.results_paths must be non-empty")
    if not self._config.ground_truth_paths:
      raise ValueError("EntryConfig.ground_truth_paths must be non-empty")

    results_config = _required_path(self._config.results_paths,
                                    "39_config",
                                    label="results_paths")
    results_programs = _required_path(self._config.results_paths,
                                      "50_programs",
                                      label="results_paths")
    results_summary7 = _required_path(self._config.results_paths,
                                      "52_summary_table7",
                                      label="results_paths")
    results_summary8 = _required_path(self._config.results_paths,
                                      "52_summary_table8",
                                      label="results_paths")

    reference_config = _required_path(self._config.ground_truth_paths,
                                      "39_config",
                                      label="ground_truth_paths")
    reference_programs = _required_path(self._config.ground_truth_paths,
                                        "50_programs",
                                        label="ground_truth_paths")
    reference_summary7 = _required_path(self._config.ground_truth_paths,
                                        "52_summary_table7",
                                        label="ground_truth_paths")
    reference_summary8 = _required_path(self._config.ground_truth_paths,
                                        "52_summary_table8",
                                        label="ground_truth_paths")

    threshold = self._config.similarity_ratio

    tables: tuple[tuple[str, Path, Path], ...] = (
        ("39_config", results_config, reference_config),
        ("50_programs", results_programs, reference_programs),
        ("52_summary_table7", results_summary7, reference_summary7),
    )

    reqs: list[utils.BaseRequirement] = []
    for name, results_path, reference_path in tables:
      # NOTE: Convert CSVs into compact numeric vectors by summing each
      # numeric column across all rows ("column totals"). This captures
      # the overall magnitude of each dependency count for individual eBPF
      # programs while being less brittle when running on eBPF program
      # versions different than those used to evaluate DepSurf.
      reqs.append(
          CSVTableModeBPearsonRequirement(
              name=f"{name}_totals",
              results_path=results_path,
              reference_path=reference_path,
              threshold=threshold,
              vector_type=_VectorType.TOTALS,
          ))
      # NOTE: Compare normalized ratios of total vounts divided by the overall sum.
      # This way, the comparison captures the distribution (shape) across dependency
      # dependnecy counts for each individual eBPF program and, like above, makes
      # the comparison more robust when using different eBPF versions than those
      # originally used to evaluate DepSurf.
      reqs.append(
          CSVTableModeBPearsonRequirement(
              name=f"{name}_fractions",
              results_path=results_path,
              reference_path=reference_path,
              threshold=threshold,
              vector_type=_VectorType.FRACTIONS,
          ))

    # NOTE: For Table 8, row keys represent stable mismatch categories,
    # so this compares per-category distributions (per numeric column)
    # using Pearson similarity.
    try:
      ref_headers8, _ref_keys8, ref_rows8 = _load_csv_matrix(
          reference_summary8, label="52_summary_table8 reference")
      numeric_cols8 = _discover_numeric_columns(
          ref_headers8, ref_rows8, label="52_summary_table8 reference")
    except ValueError:
      numeric_cols8 = ()

    if numeric_cols8:
      for col in numeric_cols8:
        safe_col = col.replace(" ", "_")
        reqs.append(
            CSVTableModeBPearsonByRowRequirement(
                name=f"52_summary_table8_{safe_col}_totals",
                results_path=results_summary8,
                reference_path=reference_summary8,
                threshold=threshold,
                column_name=col,
                vector_type=_VectorType.TOTALS,
            ))
        reqs.append(
            CSVTableModeBPearsonByRowRequirement(
                name=f"52_summary_table8_{safe_col}_fractions",
                results_path=results_summary8,
                reference_path=reference_summary8,
                threshold=threshold,
                column_name=col,
                vector_type=_VectorType.FRACTIONS,
            ))
    else:
      # NOTE: Fallback to checking aggregated column totals and ratios
      reqs.append(
          CSVTableModeBPearsonRequirement(
              name="52_summary_table8_totals",
              results_path=results_summary8,
              reference_path=reference_summary8,
              threshold=threshold,
              vector_type=_VectorType.TOTALS,
          ))
      reqs.append(
          CSVTableModeBPearsonRequirement(
              name="52_summary_table8_fractions",
              results_path=results_summary8,
              reference_path=reference_summary8,
              threshold=threshold,
              vector_type=_VectorType.FRACTIONS,
          ))

    return tuple(reqs)
