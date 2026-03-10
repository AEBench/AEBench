#!/usr/bin/env python3
"""Runs environment setup, build, benchmark prep, and experiment runs checks for EET (OSDI'24)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

from evaluator.utils import (
    EntryConfig,
    LoggerConfig,
    get_logger,
    record_result,
)

from oracle_artifact_build import OracleArtifactBuild
from oracle_benchmark_prep import OracleBenchmarkPrep
from oracle_env_setup import OracleEnvSetup
from oracle_experiment_runs import OracleExperimentRuns


def _resolve_workspace_paths() -> tuple[Path, Path, Path]:
  """Resolve and validate _agent_eval/ and eet/ locations.

  Expects either:
    (1) _agent_eval/ and eet/ are located in the same workspace root; or
    (2) _AGENT_EVAL_DIR is set to the directory containing this main.py and
        _WORKSPACE_ROOT (preferred) or _EET_HOME is set to the workspace root.
  """
  try:
    env_agent_eval = os.environ.get("_AGENT_EVAL_DIR")
    env_workspace_root = os.environ.get("_WORKSPACE_ROOT") or os.environ.get(
        "_EET_HOME")

    if env_agent_eval:
      agent_eval_dir = Path(env_agent_eval).expanduser().resolve()
    else:
      agent_eval_dir = Path(__file__).resolve().parent

    if env_workspace_root:
      workspace_root = Path(env_workspace_root).expanduser().resolve()
    else:
      workspace_root = agent_eval_dir.parent.resolve()

    if not agent_eval_dir.exists() or not agent_eval_dir.is_dir():
      raise RuntimeError(
          f"Invalid _agent_eval dir: {agent_eval_dir}\n"
          f"Set _AGENT_EVAL_DIR to the directory containing main.py if needed.")

    eet_repo_root = workspace_root / "eet"
    if not eet_repo_root.exists() or not eet_repo_root.is_dir():
      raise RuntimeError(
          f"Invalid workspace root: {workspace_root}\n"
          f"Expected to find an 'eet/' directory at: {eet_repo_root}\n"
          f"This runner expects _agent_eval/ and eet/ to be located in the same workspace root.\n"
          f"Set _WORKSPACE_ROOT (or legacy _EET_HOME) to the workspace root if needed."
      )

    eet_home = workspace_root
    return agent_eval_dir, eet_home, workspace_root

  except OSError as exc:
    raise RuntimeError(f"Failed to resolve workspace paths: {exc}") from exc


def _build_configs(*, agent_eval_dir: Path,
                   workspace_root: Path) -> EntryConfig:
  """Construct EntryConfig for the EET evaluation bundle from resolved paths."""
  canonical_benchmarks = ["mysql", "postgres", "sqlite", "clickhouse", "tidb"]

  eet_repo = (workspace_root / "eet").resolve()

  results_paths = {
      "bugs_observed_json":
          (agent_eval_dir / "outputs" / "bugs_observed.json").resolve(),
  }
  for bench in canonical_benchmarks:
    results_paths[f"{bench}_test_dir"] = (workspace_root /
                                          f"{bench}_test").resolve()

  return EntryConfig(
      name="osdi24-eet",
      home_dir=workspace_root,
      repository_paths={"osdi24-eet": eet_repo},
      results_paths=results_paths,
      ground_truth_paths={
          "bugs_expected_json":
              (agent_eval_dir / "refs" / "bugs_expected.json").resolve(),
      },
      similarity_ratio=0.75,
      metadata={
          "eet_benchmark_prep": {
              "scripts_dir": "scripts",
              "required_files": ["Dockerfile", "test_setup.sh", "run_test.sh"],
              "versions": {
                  "mysql": "8.0.34",
                  "postgres": "3f1aaaa",
                  "sqlite": "20e09ba",
                  "clickhouse": "30464b9",
                  "tidb": "f5ca27e",
              },
          },
          "benchmarks": canonical_benchmarks,
      },
  )


def main(argv: list[str]) -> int:
  verbose = "--verbose" in argv

  results: Dict[str, int] = {}
  score = 0

  logger_name = os.environ.get("EVAL_LOGGER_NAME", "EET-AGENT-EVALUATOR")
  logger = get_logger(LoggerConfig(root_name=logger_name))

  try:
    agent_eval_dir, _eet_home, workspace_root = _resolve_workspace_paths()
    eet_config = _build_configs(agent_eval_dir=agent_eval_dir,
                                workspace_root=workspace_root)
  except RuntimeError as exc:
    raise SystemExit(str(exc)) from exc

  env_checker = OracleEnvSetup(config=eet_config, logger=logger)
  env_ok = env_checker.run(verbose=verbose)
  score += record_result(results, type(env_checker).__name__, env_ok)

  build_checker = OracleArtifactBuild(config=eet_config, logger=logger)
  build_ok = build_checker.run(verbose=verbose)
  score += record_result(results, type(build_checker).__name__, build_ok)

  prep_checker = OracleBenchmarkPrep(config=eet_config, logger=logger)
  prep_ok = prep_checker.run(verbose=verbose)
  score += record_result(results, type(prep_checker).__name__, prep_ok)

  runs_checker = OracleExperimentRuns(config=eet_config, logger=logger)
  runs_ok = runs_checker.run(verbose=verbose)
  score += record_result(results, type(runs_checker).__name__, runs_ok)

  logger.info("Agent scores: %s", results)
  return score


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
