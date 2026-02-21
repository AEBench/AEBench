"""Experiment runs oracle for EET (OSDI'24).

Validates:
  - Bug totals are computed per DBMS from the produced `bugs/` directories.
  - Observed per-DBMS totals match the expected reference JSON in refs/.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from evaluator import utils
from evaluator.oracle_experiment_runs_primitives import (
    ElementwiseSimilarityThresholdRequirement,
    OracleExperimentRunsBase,
)
from evaluator.utils import EntryConfig


def _required_path(paths: Mapping[str, Path], key: str, *, label: str) -> Path:
  try:
    return paths[key]
  except KeyError as exc:
    raise KeyError(f"Missing {label} path in EntryConfig: {key!r}") from exc


def _load_json_object(path: Path) -> Any:
  try:
    with path.open("r", encoding="utf-8") as f:
      return json.load(f)
  except FileNotFoundError as exc:
    raise RuntimeError(f"JSON file not found: {path}") from exc
  except json.JSONDecodeError as exc:
    raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
  except OSError as exc:
    raise RuntimeError(f"Failed to read JSON file {path}: {exc}") from exc


def _validate_expected_mapping(obj: Any, *, path: Path) -> Dict[str, int]:
  if not isinstance(obj, dict):
    raise RuntimeError(f"Expected JSON must be an object in {path}, got: {type(obj)}")
  out: Dict[str, int] = {}
  for k, v in obj.items():
    if not isinstance(k, str):
      raise RuntimeError(f"Expected JSON keys must be strings in {path}, got key: {k!r}")
    if not isinstance(v, int):
      raise RuntimeError(f"Expected JSON values must be integers in {path}, got {k!r}: {v!r}")
    out[k] = v
  return out


def _read_meta_benchmarks(config: EntryConfig) -> list[str]:
  meta = config.metadata or {}
  obj: Any = meta.get("benchmarks")
  if isinstance(obj, (list, tuple)):
    return [str(x) for x in obj]
  return []


def _resolve_benchmark_bugs_dir(config: EntryConfig, benchmark: str) -> Path:
  direct_key = f"{benchmark}_bugs_dir"
  testdir_key = f"{benchmark}_test_dir"

  if direct_key in config.results_paths:
    return config.results_paths[direct_key]
  if testdir_key in config.results_paths:
    return config.results_paths[testdir_key] / "bugs"

  return config.home_dir / "_missing_results" / benchmark / "bugs"


def _count_bug_dirs(bugs_dir: Path) -> int:
  """Each triggered bug corresponds to one subdirectory under bugs/."""
  if not bugs_dir.exists() or not bugs_dir.is_dir():
    return 0
  try:
    return sum(1 for p in bugs_dir.iterdir() if p.is_dir())
  except OSError:
    return 0


def _ensure_parent_dir(path: Path) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: Any) -> None:
  _ensure_parent_dir(path)
  with path.open("w", encoding="utf-8") as f:
    json.dump(obj, f, indent=2, sort_keys=True)
    f.write("\n")


class OracleExperimentRuns(OracleExperimentRunsBase):
  """Experiment runs oracle for EET."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    expected_path = _required_path(
        self._config.ground_truth_paths,
        "bugs_expected_json",
        label="expected bug totals JSON",
    )
    observed_json_path = _required_path(
        self._config.results_paths,
        "bugs_observed_json",
        label="observed bug totals JSON output",
    )

    expected_obj = _load_json_object(expected_path)
    expected = _validate_expected_mapping(expected_obj, path=expected_path)

    declared = [b.lower() for b in _read_meta_benchmarks(self._config)]
    if declared:
      order = [b for b in declared if b in {k.lower() for k in expected.keys()}]
      extra = sorted(k.lower() for k in expected.keys() if k.lower() not in set(declared))
      order.extend(extra)
    else:
      order = sorted(k.lower() for k in expected.keys())

    observed_map: Dict[str, int] = {}
    observed_vec: list[float] = []
    reference_vec: list[float] = []

    expected_lc = {k.lower(): v for k, v in expected.items()}

    for bench in order:
      bugs_dir = _resolve_benchmark_bugs_dir(self._config, bench)
      obs = _count_bug_dirs(bugs_dir)
      ref = expected_lc.get(bench)

      if ref is None:
        raise RuntimeError(f"Expected JSON missing benchmark key {bench!r} (after normalization)")

      observed_map[bench] = obs
      observed_vec.append(float(obs))
      reference_vec.append(float(ref))

      self._logger.info("Bug count for %s: observed=%d expected=%d (from %s)", bench, obs, ref, bugs_dir)

    # Save observed number of bugs (debugging)
    _write_json(observed_json_path, observed_map)

    threshold = float(getattr(self._config, "similarity_ratio", 1.0))
    return [
        ElementwiseSimilarityThresholdRequirement(
            name="bugs_totals_elementwise_similarity",
            observed=observed_vec,
            reference=reference_vec,
            threshold=threshold,
        )
    ]