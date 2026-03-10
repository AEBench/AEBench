"""Benchmark preparation oracle for ANVIL (OSDI'24).

Validates:
  - Target repository working directory exists
  - Repository was cloned and is a valid git working tree
  - Current branch matches the expected branch
  - Current HEAD commit matches the expected revision
"""

from __future__ import annotations
from pathlib import Path
from typing import Sequence

from evaluator import utils
from evaluator.utils import EntryConfig
from evaluator.benchmark_prep_primitives import OracleBenchmarkPrepBase, BenchmarkRequirement, FailRequirement


class OracleBenchmarkPrep(OracleBenchmarkPrepBase):

  def __init__(self, *, config: EntryConfig, logger) -> None:
    super().__init__(logger=logger)
    self._config = config
    self._ORACLE_NAME = f"BenchmarkPrep/{config.name}"

    repo = None
    for _k, p in config.repository_paths.items():
      if p.name.lower() == "acto":
        repo = p
        break
    if repo is None and config.repository_paths:
      repo = next(iter(config.repository_paths.values()))
    self._repo_root = repo

    self._expected_branch = None
    self._expected_head = None
    if repo is not None:
      repo_id = next(k for k, v in config.repository_paths.items() if v == repo)
      bpath = config.ground_truth_paths.get(f"{repo_id}.expected_branch")
      hpath = config.ground_truth_paths.get(f"{repo_id}.expected_head")
      if bpath:
        self._expected_branch = Path(bpath).read_text(encoding="utf-8").strip()
      if hpath:
        self._expected_head = Path(hpath).read_text(encoding="utf-8").strip()

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    if self._repo_root is None:
      return (FailRequirement(name="select repo",
                              message="No repository_paths configured"),)

    reqs: list[utils.BaseRequirement] = []

    # Check that ACTO directory exists
    reqs.append(
        BenchmarkRequirement(
            name="repo directory exists",
            filepath=self._repo_root,
        ))

    # Check that ACTO repository has been cloned correctly
    reqs.append(
        BenchmarkRequirement(
            name="git working tree",
            filepath=self._repo_root,
            cmd=("git", "rev-parse", "--is-inside-work-tree"),
            signature="true",
            timeout_seconds=10.0,
        ))

    # Check that ACTO branch matches
    if not self._expected_branch:
      reqs.append(
          FailRequirement(
              name="expected branch configured",
              message=
              "Missing expected branch in EntryConfig.ground_truth_paths",
          ))
    else:
      reqs.append(
          BenchmarkRequirement(
              name="on expected branch",
              filepath=self._repo_root,
              cmd=("git", "rev-parse", "--abbrev-ref", "HEAD"),
              signature=self._expected_branch,
              timeout_seconds=10.0,
          ))

    # Check that ACTO commit SHA matches
    if not self._expected_head:
      reqs.append(
          FailRequirement(
              name="expected head configured",
              message="Missing expected head in EntryConfig.ground_truth_paths",
          ))
    else:
      reqs.append(
          BenchmarkRequirement(
              name="HEAD matches expected",
              filepath=self._repo_root,
              cmd=("git", "rev-parse", "HEAD"),
              signature=self._expected_head,
              timeout_seconds=10.0,
          ))

    return tuple(reqs)
