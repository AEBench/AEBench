#!/usr/bin/env python3
"""Runs environment setup, build, benchmark prep, and experiment runs checks for EGWALKER (EuroSys'25)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import os
import sys


_AGENT_EVAL_DIR = Path(__file__).resolve().parent
_AGENT_SRC_DIR = _AGENT_EVAL_DIR.parents[3] / "src"
sys.path.append(str(_AGENT_SRC_DIR))


from evaluator.utils import (
  EntryConfig,
  LoggerConfig,
  get_logger,
  record_result,
)
from oracle_artifact_build import OracleArtifactBuild
from oracle_env_setup import OracleEnvSetup
from oracle_benchmark_prep import OracleBenchmarkPrep
from oracle_experiment_runs import OracleExperimentRuns


def _resolve_workspace_paths() -> tuple[Path, Path, Path]:
  """Resolve and validate _agent_eval/ and egwalker/ locations.
  This expectes that either:
    (1) _agent_eval/ and egwalker/ are located in the same root directory; or
    (2) _AGENT_EVAL_DIR and _EGWALKER_HOME are set by the user
  """
  try:
    env_agent_eval = os.environ.get("_AGENT_EVAL_DIR")
    env_egwalker_home = os.environ.get("_EGWALKER_HOME")

    if env_agent_eval:
      agent_eval_dir = Path(env_agent_eval).expanduser().resolve()
    else:
      agent_eval_dir = Path(__file__).resolve().parent

    if env_egwalker_home:
      egwalker_home = Path(env_egwalker_home).expanduser().resolve()
    else:
      egwalker_home = agent_eval_dir.parent.resolve()

    if not agent_eval_dir.exists() or not agent_eval_dir.is_dir():
      raise RuntimeError(
          f"Invalid _agent_eval dir: {agent_eval_dir}\n"
          f"This runner expects _agent_eval/ and egwalker/ to be located in the same root directory.\n"
          f"Set _AGENT_EVAL_DIR to the directory containing main.py if needed."
      )

    egwalker_repo_root = egwalker_home / "egwalker"
    if not egwalker_repo_root.exists() or not egwalker_repo_root.is_dir():
      raise RuntimeError(
          f"Invalid EGWALKER workspace: {egwalker_home}\n"
          f"Expected to find a 'egwalker/' directory at: {egwalker_repo_root}\n"
          f"This runner expects _agent_eval/ and egwalker/ to be located in the same root directory.\n"
          f"Set _EGWALKER_HOME to the workspace root if needed."
      )

    workspace_root = egwalker_home
    return agent_eval_dir, workspace_root

  except OSError as exc:
    raise RuntimeError(f"Failed to resolve workspace paths: {exc}") from exc


def _build_egwalker_config(*, agent_eval_dir: Path, workspace_root: Path) -> EntryConfig:
  """Constructs EntryConfig for the EGWALKER evaluation bundle from resolved paths."""
  egwalker_repo = (workspace_root / "egwalker").resolve()
  egwalker_agent_eval = agent_eval_dir.resolve()
  egwalker_refs = (egwalker_agent_eval / "refs").resolve()
  egwalker_results = (egwalker_repo / "results").resolve()

  return EntryConfig(
    name = "eurosys25-egwalker",
    home_dir = workspace_root,
    repository_paths = {
      "eurosys25-egwalker": egwalker_repo,
    },
    results_paths = {
      "timings": egwalker_results / "timings.json",
    },
    ground_truth_paths = {
      "datasets": egwalker_refs / "datasets.ref.json",
      "timings": egwalker_refs / "timings.ref.json",
    },
    similarity_ratio = 0.75,
  )


def main(argv: list[str]) -> int:
  verbose = "--verbose" in argv

  results: Dict[str, int] = {}
  score = 0

  logger_name = os.environ.get("EVAL_LOGGER_NAME", "EGWALKER-AGENT-EVALUATOR")
  logger = get_logger(LoggerConfig(root_name = logger_name))

  try:
    agent_eval_dir, workspace_root = _resolve_workspace_paths()
    EGWALKER_CONFIG = _build_egwalker_config(agent_eval_dir = agent_eval_dir, workspace_root = workspace_root)
  except RuntimeError as exc:
    raise SystemExit(str(exc)) from exc

  env_checker = OracleEnvSetup(config = EGWALKER_CONFIG, logger = logger)
  score += record_result(results, type(env_checker).__name__, env_checker.run(verbose = verbose))

  build_checker = OracleArtifactBuild(config = EGWALKER_CONFIG, logger = logger)
  score += record_result(results, type(build_checker).__name__, build_checker.run(verbose = verbose))

  prep_checker = OracleBenchmarkPrep(config = EGWALKER_CONFIG, logger = logger)
  score += record_result(results, type(prep_checker).__name__, prep_checker.run(verbose = verbose))

  runs_checker = OracleExperimentRuns(config = EGWALKER_CONFIG, logger = logger)
  score += record_result(results, type(runs_checker).__name__, runs_checker.run(verbose = verbose))

  logger.info("Agent scores: %s", results)
  return score


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
