"""Experiment runs oracle for ANVIL (OSDI'24).

Validates:
  - Table 3 results file exists and is parseable
  - Table 3 reference (ground-truth) JSON exists and is parseable
  - Per-controller mean and max ratios (verified/reference) meet similarity thresholds
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from evaluator import utils
from evaluator.oracle_experiment_runs_primitives import (
    ElementwiseSimilarityThresholdRequirement,
    ListSimilarityRequirement,
    SimilarityMetric,
    OracleExperimentRunsBase,
)
from evaluator.utils import EntryConfig


@dataclass(frozen=True, slots=True)
class TableRow:
  controller: str
  verified_anvil_mean: float
  verified_anvil_max: float
  reference_unverified_mean: float
  reference_unverified_max: float


_EXPECTED_HEADERS: tuple[str, ...] = (
    "Controller",
    "Verified (Anvil) Mean",
    "Verified (Anvil) Max",
    "Reference (unverified) Mean",
    "Reference (unverified) Max",
)


def _required_path(paths: Mapping[str, Path], key: str, *, label: str) -> Path:
  """Returns a required path from a mapping with a clear error message."""
  try:
    return paths[key]
  except KeyError as exc:
    raise ValueError(f"Missing {label}[{key!r}] in EntryConfig") from exc


def _read_lines(path: Path, *, label: str) -> list[str]:
  if not path.exists():
    raise ValueError(f"{label}: {path} not found")
  return path.read_text(encoding="utf-8").splitlines()


def _is_separator_line(line: str) -> bool:
  """Returns True if this looks like the Markdown header separator line."""
  stripped = line.strip()
  if not stripped.startswith("|") or not stripped.endswith("|"):
    return False
  inner = stripped.replace("|", "").replace(" ", "")
  return bool(inner) and all(ch in "-:" for ch in inner)


def _split_markdown_row(line: str) -> list[str]:
  """Splits a markdown table row into cells."""
  return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_float_token(text: str, *, label: str) -> float:
  """Parses a float allowing commas."""
  try:
    return float(text.replace(",", ""))
  except ValueError as exc:
    raise ValueError(f"{label}: unparseable float: {text!r}") from exc


def _compute_ratios(row: TableRow) -> tuple[float, float]:
  """Computes (mean_ratio, max_ratio) as verified/reference per row."""
  if row.reference_unverified_mean == 0.0:
    mean_ratio = float("inf")
  else:
    mean_ratio = row.verified_anvil_mean / row.reference_unverified_mean

  if row.reference_unverified_max == 0.0:
    max_ratio = float("inf")
  else:
    max_ratio = row.verified_anvil_max / row.reference_unverified_max

  return mean_ratio, max_ratio


def _parse_results_table_rows(lines: Sequence[str]) -> list[TableRow]:
  """Parses the markdown table from results into rows."""
  header_line: str | None = None
  data_lines: list[str] = []

  for line in lines:
    if "|" not in line:
      continue

    if header_line is None:
      header_line = line
      continue

    if _is_separator_line(line):
      continue

    data_lines.append(line)

  if header_line is None:
    raise ValueError("No table header found")

  headers = _split_markdown_row(header_line)
  if tuple(headers) != _EXPECTED_HEADERS:
    raise ValueError(f"Unexpected table headers: {headers!r}")

  rows: list[TableRow] = []
  for line in data_lines:
    cells = _split_markdown_row(line)
    if len(cells) != len(_EXPECTED_HEADERS):
      raise ValueError(
          f"Row has {len(cells)} cells, expected {len(_EXPECTED_HEADERS)}: {line!r}"
      )

    controller = cells[0]
    verified_anvil_mean = _parse_float_token(cells[1],
                                             label="Verified (Anvil) Mean")
    verified_anvil_max = _parse_float_token(cells[2],
                                            label="Verified (Anvil) Max")
    reference_unverified_mean = _parse_float_token(
        cells[3], label="Reference (unverified) Mean")
    reference_unverified_max = _parse_float_token(
        cells[4], label="Reference (unverified) Max")

    rows.append(
        TableRow(
            controller=controller,
            verified_anvil_mean=verified_anvil_mean,
            verified_anvil_max=verified_anvil_max,
            reference_unverified_mean=reference_unverified_mean,
            reference_unverified_max=reference_unverified_max,
        ))

  return rows


def _load_reference_rows(path: Path) -> list[TableRow]:
  """Loads reference TableRow objects from JSON (list of row objects)."""
  if not path.exists():
    raise ValueError(f"{path} not found")

  try:
    raw = json.loads(path.read_text(encoding="utf-8"))
  except json.JSONDecodeError as exc:
    raise ValueError(f"{path} invalid JSON: {exc}") from exc

  if not isinstance(raw, list):
    raise ValueError(f"{path} must contain a list of objects")

  rows: list[TableRow] = []
  for idx, obj in enumerate(raw):
    if not isinstance(obj, dict):
      raise ValueError(f"{path} entry #{idx} is not an object")

    try:
      rows.append(
          TableRow(
              controller=str(obj["controller"]),
              verified_anvil_mean=float(obj["verified_anvil_mean"]),
              verified_anvil_max=float(obj["verified_anvil_max"]),
              reference_unverified_mean=float(obj["reference_unverified_mean"]),
              reference_unverified_max=float(obj["reference_unverified_max"]),
          ))
    except (KeyError, TypeError, ValueError) as exc:
      raise ValueError(f"{path} malformed entry #{idx}: {exc}") from exc

  return rows


def _ratios_by_controller(
    rows: Sequence[TableRow]) -> dict[str, tuple[float, float]]:
  """Returns controller -> (mean_ratio, max_ratio)."""
  out: dict[str, tuple[float, float]] = {}
  for r in rows:
    if r.controller in out:
      raise ValueError(f"Duplicate controller row: {r.controller!r}")
    out[r.controller] = _compute_ratios(r)
  return out


def _controller_id_as_float(controller: str) -> float:
  """Determinstic controller ID encoding as a float."""
  digest = hashlib.sha256(controller.encode("utf-8")).digest()
  raw64 = int.from_bytes(digest[:8], "big", signed=False)
  return float(raw64 % (2**53))


class OracleExperimentRuns(OracleExperimentRunsBase):
  """Validates ANVIL experiment run outputs (Table 3)."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    if not self._config.results_paths:
      raise ValueError("EntryConfig.results_paths must be non-empty")
    if not self._config.ground_truth_paths:
      raise ValueError("EntryConfig.ground_truth_paths must be non-empty")

    results_path = _required_path(self._config.results_paths,
                                  "table3",
                                  label="results_paths")
    reference_path = _required_path(self._config.ground_truth_paths,
                                    "table3",
                                    label="ground_truth_paths")

    threshold = self._config.similarity_ratio

    results_rows = _parse_results_table_rows(
        _read_lines(results_path, label="results_path"))
    reference_rows = _load_reference_rows(reference_path)

    results_map = _ratios_by_controller(results_rows)
    reference_map = _ratios_by_controller(reference_rows)

    controllers_union = sorted(set(results_map) | set(reference_map))
    results_mean = []
    ref_mean = []
    results_max = []
    ref_max = []
    for c in controllers_union:
      r = results_map.get(c)
      g = reference_map.get(c)
      results_mean.append(r[0] if r is not None else float("nan"))
      results_max.append(r[1] if r is not None else float("nan"))
      ref_mean.append(g[0] if g is not None else float("nan"))
      ref_max.append(g[1] if g is not None else float("nan"))

    results_controller_ids = [
        _controller_id_as_float(c) for c in results_map.keys()
    ]
    reference_controller_ids = [
        _controller_id_as_float(c) for c in reference_map.keys()
    ]

    return (
        ListSimilarityRequirement(
            name="table3_controllers",
            observed=results_controller_ids,
            reference=reference_controller_ids,
            metric=SimilarityMetric.JACCARD_SET,
            min_similarity=1.0,
        ),
        ElementwiseSimilarityThresholdRequirement(
            name="table3_mean_ratio",
            observed=results_mean,
            reference=ref_mean,
            threshold=threshold,
        ),
        ElementwiseSimilarityThresholdRequirement(
            name="table3_max_ratio",
            observed=results_max,
            reference=ref_max,
            threshold=threshold,
        ),
    )
