#!/usr/bin/env python3
"""Runs environment setup, build, benchmark prep, and experiment runs checks for ANVIL (OSDI'24)."""

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
from oracle_env_setup import OracleEnvSetup
from oracle_artifact_build import OracleArtifactBuild
from oracle_benchmark_prep import OracleBenchmarkPrep
from oracle_experiment_runs import OracleExperimentRuns


def _resolve_workspace_paths() -> tuple[Path, Path]:
  """Resolve and validate _agent_eval/ and the ANVIL workspace root.

  Expects either:
    (1) _agent_eval/ and (anvil/, acto/) are located in the same root directory; or
    (2) _AGENT_EVAL_DIR and _ANVIL_HOME are set by the user.
  """
  try:
    env_agent_eval = os.environ.get("_AGENT_EVAL_DIR")
    env_anvil_home = os.environ.get("_ANVIL_HOME")

    if env_agent_eval:
      agent_eval_dir = Path(env_agent_eval).expanduser().resolve()
    else:
      agent_eval_dir = Path(__file__).resolve().parent

    if env_anvil_home:
      workspace_root = Path(env_anvil_home).expanduser().resolve()
    else:
      workspace_root = agent_eval_dir.parent.resolve()

    if not agent_eval_dir.exists() or not agent_eval_dir.is_dir():
      raise RuntimeError(
          f"Invalid _agent_eval dir: {agent_eval_dir}\n"
          f"This runner expects _agent_eval/ to exist.\n"
          f"Set _AGENT_EVAL_DIR to the directory containing main.py if needed.")

    anvil_repo_root = workspace_root / "anvil"
    if not anvil_repo_root.exists() or not anvil_repo_root.is_dir():
      raise RuntimeError(
          f"Invalid ANVIL workspace: {workspace_root}\n"
          f"Expected to find an 'anvil/' directory at: {anvil_repo_root}\n"
          f"This runner expects _agent_eval/ and anvil/ to be located in the same root directory.\n"
          f"Set _ANVIL_HOME to the workspace root if needed.")

    acto_repo_root = workspace_root / "acto"
    if not acto_repo_root.exists() or not acto_repo_root.is_dir():
      raise RuntimeError(
          f"Invalid ANVIL workspace: {workspace_root}\n"
          f"Expected to find an 'acto/' directory at: {acto_repo_root}\n"
          f"This runner expects _agent_eval/ and acto/ to be located in the same root directory.\n"
          f"Set _ANVIL_HOME to the workspace root if needed.")

    return agent_eval_dir, workspace_root

  except OSError as exc:
    raise RuntimeError(f"Failed to resolve workspace paths: {exc}") from exc


def _build_anvil_config(*, agent_eval_dir: Path,
                        workspace_root: Path) -> EntryConfig:
  """Construct EntryConfig for the ANVIL evaluation bundle from resolved paths."""
  anvil_repo = (workspace_root / "anvil").resolve()
  acto_repo = (workspace_root / "acto").resolve()

  agent_eval_dir = agent_eval_dir.resolve()
  refs_dir = (agent_eval_dir / "refs").resolve()

  default_table3_results = (anvil_repo / "results" / "table3.md").resolve()
  table3_results = Path(
      os.environ.get("_ANVIL_TABLE3_RESULTS",
                     str(default_table3_results))).expanduser().resolve()

  similarity_ratio = float(os.environ.get("_ANVIL_SIMILARITY_RATIO", "0.75"))

  return EntryConfig(
      name="osdi24-anvil",
      home_dir=workspace_root,
      repository_paths={
          "osdi24-anvil": anvil_repo,
          "osdi24-acto-dependency": acto_repo,
      },
      results_paths={
          "table3": table3_results,
      },
      ground_truth_paths={
          "table3": (refs_dir / "anvil-table-3.ref.json").resolve(),
          "osdi24-acto-dependency.expected_branch":
              (refs_dir / "acto.expected_branch.txt").resolve(),
          "osdi24-acto-dependency.expected_head":
              (refs_dir / "acto.expected_head.txt").resolve(),
      },
      similarity_ratio=similarity_ratio,
  )


def main(argv: list[str]) -> int:
  verbose = "--verbose" in argv

  results: Dict[str, int] = {}
  score = 0

  logger_name = os.environ.get("EVAL_LOGGER_NAME", "ANVIL-AGENT-EVALUATOR")
  logger = get_logger(LoggerConfig(root_name=logger_name))

  try:
    agent_eval_dir, workspace_root = _resolve_workspace_paths()
    ANVIL_CONFIG = _build_anvil_config(agent_eval_dir=agent_eval_dir,
                                       workspace_root=workspace_root)
  except RuntimeError as exc:
    raise SystemExit(str(exc)) from exc

  env_checker = OracleEnvSetup(config=ANVIL_CONFIG, logger=logger)
  score += record_result(results,
                         type(env_checker).__name__,
                         env_checker.run(verbose=verbose))

  build_checker = OracleArtifactBuild(config=ANVIL_CONFIG, logger=logger)
  score += record_result(results,
                         type(build_checker).__name__,
                         build_checker.run(verbose=verbose))

  prep_checker = OracleBenchmarkPrep(config=ANVIL_CONFIG, logger=logger)
  score += record_result(results,
                         type(prep_checker).__name__,
                         prep_checker.run(verbose=verbose))

  runs_checker = OracleExperimentRuns(config=ANVIL_CONFIG, logger=logger)
  score += record_result(results,
                         type(runs_checker).__name__,
                         runs_checker.run(verbose=verbose))

  logger.info("Agent scores: %s", results)
  return score


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
