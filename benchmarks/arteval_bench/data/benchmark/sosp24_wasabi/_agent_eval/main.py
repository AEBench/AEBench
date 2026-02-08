#!/usr/bin/env python3
"""Runs environment setup checks for WASABI."""

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


def _resolve_workspace_paths() -> tuple[Path, Path, Path]:
  """Resolve and validate _agent_eval/ and wasabi/ locations.
  This expectes that either:
    (1) _agent_eval/ and wasabi/ are located in the same root directory; or
    (2) _AGENT_EVAL_DIR and _WASABI_HOME are set by the user
  """
  try:
    env_agent_eval = os.environ.get("_AGENT_EVAL_DIR")
    env_wasabi_home = os.environ.get("_WASABI_HOME")

    if env_agent_eval:
      agent_eval_dir = Path(env_agent_eval).expanduser().resolve()
    else:
      agent_eval_dir = Path(__file__).resolve().parent

    if env_wasabi_home:
      wasabi_home = Path(env_wasabi_home).expanduser().resolve()
    else:
      wasabi_home = agent_eval_dir.parent.resolve()

    if not agent_eval_dir.exists() or not agent_eval_dir.is_dir():
      raise RuntimeError(
          f"Invalid _agent_eval dir: {agent_eval_dir}\n"
          f"This runner expects _agent_eval/ and wasabi/ to be located in the same root directory.\n"
          f"Set _AGENT_EVAL_DIR to the directory containing main.py if needed."
      )

    wasabi_repo_root = wasabi_home / "wasabi"
    if not wasabi_repo_root.exists() or not wasabi_repo_root.is_dir():
      raise RuntimeError(
          f"Invalid WASABI workspace: {wasabi_home}\n"
          f"Expected to find a 'wasabi/' directory at: {wasabi_repo_root}\n"
          f"This runner expects _agent_eval/ and wasabi/ to be located in the same root directory.\n"
          f"Set _WASABI_HOME to the workspace root if needed."
      )

    workspace_root = wasabi_home
    return agent_eval_dir, wasabi_home, workspace_root

  except OSError as exc:
    raise RuntimeError(f"Failed to resolve workspace paths: {exc}") from exc


def _build_configs(*, agent_eval_dir: Path, workspace_root: Path) -> EntryConfig:
  """Constructs EntryConfig for the WASABI evaluation bundle from resolved paths."""
  wasabi_repo = (workspace_root / "wasabi").resolve()
  benchmarks_dir = (workspace_root / "benchmarks").resolve()

  return EntryConfig(
      name="sosp24-wasabi",
      home_dir=workspace_root,
      repository_paths={
          "sosp24-wasabi": wasabi_repo,
          "benchmarks": benchmarks_dir,
      },
      results_paths={
          "results_root": wasabi_repo / "results",
      },
      ground_truth_paths={
          "bugs_ground_truth": agent_eval_dir / "refs" / "bugs_ground_truth.csv",
      },
      similarity_ratio=0.75,
      metadata={
          "maven_repo_dir": Path.home() / ".m2" / "repository",
          "weaving_plugin_signature": "aspectj-maven-plugin",
          "primary_artifact": "edu.uchicago.cs.systems:wasabi",
          "benchmarks": {
              "hadoop": {
                  "repo_url": "https://github.com/apache/hadoop.git",
                  "commit": "60867de",
                  "pom_file": "pom.xml",
                  "pom_backup": "pom-original.xml",
              },
              "hbase": {
                  "repo_url": "https://github.com/apache/hbase.git",
                  "commit": "89ca7f4",
                  "pom_file": "pom.xml",
                  "pom_backup": "pom-original.xml",
              },
              "hive": {
                  "repo_url": "https://github.com/apache/hive.git",
                  "commit": "e08a600",
                  "pom_file": "pom.xml",
                  "pom_backup": "pom-original.xml",
              },
          },
          "aspectj_markers": [
              "ajc$preClinit",
              "ajc$initFailureCause",
              "ajc$tjp",
              "ajc$before$",
              "ajc$after$",
              "ajc$around$",
              "ajc$interField$",
              "ajc$interMethod$",
              "org.aspectj.runtime.reflect.Factory",
              "org.aspectj.runtime.internal.AroundClosure",
              "org.aspectj.lang.JoinPoint",
              "org.aspectj.lang.JoinPoint$StaticPart",
              "org.aspectj.lang.ProceedingJoinPoint",
              "org.aspectj.lang.Signature",
              "org.aspectj.lang.NoAspectBoundException",
          ],
      },
  )


def main(argv: list[str]) -> int:
  verbose = "--verbose" in argv

  results: Dict[str, int] = {}
  score = 0

  logger_name = os.environ.get("EVAL_LOGGER_NAME", "WASABI-AGENT-EVALUATOR")
  logger = get_logger(LoggerConfig(root_name=logger_name))

  try:
    agent_eval_dir, _wasabi_home, workspace_root = _resolve_workspace_paths()
    wasabi_config = _build_configs(agent_eval_dir=agent_eval_dir, workspace_root=workspace_root)
  except RuntimeError as exc:
    # Keep failure message clean and actionable
    raise SystemExit(str(exc)) from exc

  env_checker = OracleEnvSetup(config=wasabi_config, logger=logger)
  env_ok = env_checker.run(verbose=verbose)
  score += record_result(results, type(env_checker).__name__, env_ok)

  build_checker = OracleArtifactBuild(config=wasabi_config, logger=logger)
  build_ok = build_checker.run(verbose=verbose)
  score += record_result(results, type(build_checker).__name__, build_ok)

  prep_checker = OracleBenchmarkPrep(config=wasabi_config, logger=logger)
  prep_ok = prep_checker.run(verbose=verbose)
  score += record_result(results, type(prep_checker).__name__, prep_ok)

  runs_checker = OracleExperimentRuns(config=wasabi_config, logger=logger)
  runs_ok = runs_checker.run(verbose=verbose)
  score += record_result(results, type(runs_checker).__name__, runs_ok)

  logger.info("Agent scores: %s", results)
  return score


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
