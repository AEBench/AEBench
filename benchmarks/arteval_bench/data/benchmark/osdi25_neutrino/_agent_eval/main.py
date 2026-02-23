#!/usr/bin/env python3
"""Runs environment setup, build, benchmark prep, and experiment runs checks for Neutrino (OSDI'25)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple
import os
import sys


from evaluator.utils import (  # noqa: E402
    EntryConfig,
    LoggerConfig,
    get_logger,
    record_result,
)

from oracle_artifact_build import OracleArtifactBuild  # noqa: E402
from oracle_benchmark_prep import OracleBenchmarkPrep  # noqa: E402
from oracle_env_setup import OracleEnvSetup  # noqa: E402
from oracle_experiment_runs import OracleExperimentRuns  # noqa: E402


def _resolve_workspace_paths() -> Tuple[Path, Path, Path]:
    """Resolve and validate _agent_eval/ and neutrino/ locations.

    Expects either:
      (1) _agent_eval/ and the Neutrino repo are located in the same workspace root; or
      (2) _AGENT_EVAL_DIR and _NEUTRINO_HOME are set by the user.
    """
    try:
        env_agent_eval = os.environ.get("_AGENT_EVAL_DIR")
        env_neutrino_home = os.environ.get("_NEUTRINO_HOME")

        agent_eval_dir = (
            Path(env_agent_eval).expanduser().resolve()
            if env_agent_eval
            else Path(__file__).resolve().parent
        )

        workspace_root = (
            Path(env_neutrino_home).expanduser().resolve()
            if env_neutrino_home
            else agent_eval_dir.parent.resolve()
        )

        if not agent_eval_dir.is_dir():
            raise RuntimeError(
                f"Invalid _agent_eval dir: {agent_eval_dir}\n"
                "Set _AGENT_EVAL_DIR to the directory containing main.py if needed."
            )

        neutrino_repo_root = workspace_root / "neutrino"
        if not neutrino_repo_root.is_dir():
            raise RuntimeError(
                f"Invalid Neutrino workspace: {workspace_root}\n"
                f"Expected to find a Neutrino repository directory at: {neutrino_repo_root}\n"
                "This runner expects _agent_eval/ and the Neutrino repo to be located in the same workspace root.\n"
                "Set _NEUTRINO_HOME to the workspace root if needed."
            )

        return agent_eval_dir, workspace_root, neutrino_repo_root

    except OSError as exc:
        raise RuntimeError(f"Failed to resolve workspace paths: {exc}") from exc


def _build_neutrino_config(
    *, agent_eval_dir: Path, workspace_root: Path, neutrino_repo_root: Path
) -> EntryConfig:
    """Constructs EntryConfig for the Neutrino evaluation bundle from resolved paths."""
    
    return EntryConfig(
        name="osdi25-neutrino",
        home_dir=workspace_root,
        repository_paths={
            "osdi25-neutrino": neutrino_repo_root,
        },
        results_paths={
            # Need to add results dir
        },
        ground_truth_paths={
            # Need _agent_eval/refs.
        },
        similarity_ratio=0.75,
    )


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv

    results: Dict[str, int] = {}
    score = 0

    logger_name = os.environ.get("EVAL_LOGGER_NAME", "NEUTRINO-AGENT-EVALUATOR")
    logger = get_logger(LoggerConfig(root_name=logger_name))

    try:
        agent_eval_dir, workspace_root, neutrino_repo_root = _resolve_workspace_paths()
        NEUTRINO_CONFIG = _build_neutrino_config(
            agent_eval_dir=agent_eval_dir,
            workspace_root=workspace_root,
            neutrino_repo_root=neutrino_repo_root,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    env_checker = OracleEnvSetup(config=NEUTRINO_CONFIG, logger=logger)
    score += record_result(results, type(env_checker).__name__, env_checker.run(verbose=verbose))

    build_checker = OracleArtifactBuild(config=NEUTRINO_CONFIG, logger=logger)
    score += record_result(results, type(build_checker).__name__, build_checker.run(verbose=verbose))

    prep_checker = OracleBenchmarkPrep(config=NEUTRINO_CONFIG, logger=logger)
    score += record_result(results, type(prep_checker).__name__, prep_checker.run(verbose=verbose))

    runs_checker = OracleExperimentRuns(config=NEUTRINO_CONFIG, logger=logger)
    score += record_result(results, type(runs_checker).__name__, runs_checker.run(verbose=verbose))

    logger.info("Agent scores: %s", results)
    return score


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))