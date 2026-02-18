#!/usr/bin/env python3
"""Runs environment setup, build, benchmark prep, and experiment runs checks for DepSurf."""

from __future__ import annotations

from pathlib import Path
from typing import Dict
import os
import sys

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


def _resolve_workspace_paths() -> tuple[Path, Path]:
  """Resolve and validate _agent_eval/ and DepSurf/ locations.
  This expectes that either:
    (1) _agent_eval/ and DepSurf/ are located in the same root directory; or
    (2) _AGENT_EVAL_DIR and _DEPSURF_HOME are set by the user
  """
  try:
    env_agent_eval = os.environ.get("_AGENT_EVAL_DIR")
    env_depsurf_home = os.environ.get("_DEPSURF_HOME")

    if env_agent_eval:
      agent_eval_dir = Path(env_agent_eval).expanduser().resolve()
    else:
      agent_eval_dir = Path(__file__).resolve().parent

    if env_depsurf_home:
      depsurf_home = Path(env_depsurf_home).expanduser().resolve()
    else:
      depsurf_home = agent_eval_dir.parent.resolve()

    if not agent_eval_dir.exists() or not agent_eval_dir.is_dir():
      raise RuntimeError(
          f"Invalid _agent_eval dir: {agent_eval_dir}\n"
          f"This runner expects _agent_eval/ and DepSurf/ to be located in the same root directory.\n"
          f"Set _AGENT_EVAL_DIR to the directory containing main.py if needed.")

    depsurf_repo_root = depsurf_home / "DepSurf"
    if not depsurf_repo_root.exists() or not depsurf_repo_root.is_dir():
      raise RuntimeError(
          f"Invalid DepSurf workspace: {depsurf_home}\n"
          f"Expected to find a 'DepSurf/' directory at: {depsurf_repo_root}\n"
          f"This runner expects _agent_eval/ and DepSurf/ to be located in the same root directory.\n"
          f"Set _DEPSURF_HOME to the workspace root if needed.")

    workspace_root = depsurf_home
    return agent_eval_dir, workspace_root

  except OSError as exc:
    raise RuntimeError(f"Failed to resolve workspace paths: {exc}") from exc


def _build_depsurf_config(*, agent_eval_dir: Path,
                          workspace_root: Path) -> EntryConfig:
  """Constructs EntryConfig for the DepSurf evaluation bundle from resolved paths."""
  depsurf_repo = (workspace_root / "DepSurf").resolve()
  depsurf_agent_eval = agent_eval_dir.resolve()
  depsurf_refs = (depsurf_agent_eval / "refs").resolve()
  depsurf_results = (depsurf_repo / "results").resolve()

  return EntryConfig(
      name="depsurf",
      home_dir=workspace_root,
      repository_paths={
          "depsurf": depsurf_repo,
      },
      results_paths={
          "39_config": depsurf_results / "39_config.csv",
          "50_programs": depsurf_results / "50_programs.csv",
          "52_summary_table7": depsurf_results / "52_summary_table7.csv",
          "52_summary_table8": depsurf_results / "52_summary_table8.csv",
      },
      ground_truth_paths={
          "39_config": depsurf_refs / "39_config.csv",
          "50_programs": depsurf_refs / "50_programs.csv",
          "52_summary_table7": depsurf_refs / "52_summary_table7.csv",
          "52_summary_table8": depsurf_refs / "52_summary_table8.csv",
      },
      metadata={
          # Dataset main directory, relative to the DepSurf root
          "dataset_relpath": "data/dataset",
          # Required subdirectories in the dataset main directory
          "dataset_subdirs": [
              "comment",
              "config",
              "func_groups",
              "symtab",
              "syscalls",
              "tracepoints",
              "types_enum",
              "types_func",
              "types_int",
              "types_struct",
              "types_union",
          ],
          # Required files in the dataset (base names only)
          "dataset_basenames": [
              "4.10.0-19-generic-amd64",
              "4.13.0-16-generic-amd64",
              "4.15.0-20-generic-amd64",
              "4.18.0-10-generic-amd64",
              "4.4.0-21-generic-amd64",
              "4.8.0-22-generic-amd64",
              "5.0.0-13-generic-amd64",
              "5.11.0-16-generic-amd64",
              "5.13.0-19-generic-amd64",
              "5.15.0-25-generic-amd64",
              "5.19.0-21-generic-amd64",
              "5.3.0-18-generic-amd64",
              "5.4.0-1009-aws-amd64",
              "5.4.0-1009-gcp-amd64",
              "5.4.0-1010-azure-amd64",
              "5.4.0-24-generic-riscv64",
              "5.4.0-26-generic-amd64",
              "5.4.0-26-generic-arm64",
              "5.4.0-26-generic-armhf",
              "5.4.0-26-generic-ppc64el",
              "5.4.0-26-lowlatency-amd64",
              "5.8.0-25-generic-amd64",
              "6.2.0-20-generic-amd64",
              "6.5.0-9-generic-amd64",
              "6.8.0-31-generic-amd64",
          ],
          # Optional. Leave empty to use "<basename> OR <basename>.*" existence policy.
          # If later you confirm each subdir has a fixed extension, populate this mapping.
          "dataset_subdir_suffixes": {},
      },
      similarity_ratio=0.75,
  )


def main(argv: list[str]) -> int:
  verbose = "--verbose" in argv

  results: Dict[str, int] = {}
  score = 0

  logger_name = os.environ.get("EVAL_LOGGER_NAME", "DEPSURF-AGENT-EVALUATOR")
  logger = get_logger(LoggerConfig(root_name=logger_name))

  try:
    agent_eval_dir, workspace_root = _resolve_workspace_paths()
    DEPSURF_CONFIG = _build_depsurf_config(agent_eval_dir=agent_eval_dir,
                                           workspace_root=workspace_root)
  except RuntimeError as exc:
    raise SystemExit(str(exc)) from exc

  env_checker = OracleEnvSetup(config=DEPSURF_CONFIG, logger=logger)
  score += record_result(results,
                         type(env_checker).__name__,
                         env_checker.run(verbose=verbose))

  build_checker = OracleArtifactBuild(config=DEPSURF_CONFIG, logger=logger)
  score += record_result(results,
                         type(build_checker).__name__,
                         build_checker.run(verbose=verbose))

  prep_checker = OracleBenchmarkPrep(config=DEPSURF_CONFIG, logger=logger)
  score += record_result(results,
                         type(prep_checker).__name__,
                         prep_checker.run(verbose=verbose))

  runs_checker = OracleExperimentRuns(config=DEPSURF_CONFIG, logger=logger)
  score += record_result(results,
                         type(runs_checker).__name__,
                         runs_checker.run(verbose=verbose))

  logger.info("Agent scores: %s", results)
  return score


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
