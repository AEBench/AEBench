"""Benchmark preparation oracle for EET (OSDI'24).

Validates:
  - Scripts directory layout and main scripts are present.
  - Each ./scripts/<benchmark>/run_test.sh script contains the expected version/commit version.
"""

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluator import utils
from evaluator.utils import EntryConfig
from evaluator.oracle_benchmark_prep_primitives import (
    BenchmarkRequirement,
    FailRequirement,
    OracleBenchmarkPrepBase,
)


def _read_meta_dict(config: EntryConfig, key: str) -> Mapping[str, Any] | None:
  """Returns a EntryConfig entry, or None if missing/invalid."""
  obj: Any = config.metadata.get(key)
  if isinstance(obj, dict):
    return obj
  return None


def _require_path(
    reqs: list[utils.BaseRequirement],
    *,
    name: str,
    path: Path,
    req_type: str,
) -> None:
  """Append requirements for path existence and expected type (file/dir)."""
  reqs.append(BenchmarkRequirement(name=f"{name}_exists", filepath=path))

  if not path.exists():
    return

  if req_type is "dir" and not path.is_dir():
    reqs.append(
        FailRequirement(
            name=f"{name}_is_directory",
            message=f"expected directory, found non-directory path: {path}",
        ))
  elif req_type is "file" and not path.is_file():
    reqs.append(
        FailRequirement(
            name=f"{name}_is_file",
            message=f"expected file, found non-file path: {path}",
        ))
  else:
    raise ValueError(f"Unknown type: {req_type}")


class OracleBenchmarkPrep(OracleBenchmarkPrepBase):
  """Validates dataset prerequisites for _agent_eval bundles."""

  def __init__(self, *, config: EntryConfig, logger: logging.Logger) -> None:
    super().__init__(logger=logger)
    self._config = config

  def requirements(self) -> Sequence[utils.BaseRequirement]:
    reqs: list[utils.BaseRequirement] = []

    repo_root = Path(self._config.repository_paths.get(self._config.name))
    if repo_root is None:
      return [
          FailRequirement(
              name="config:repo_root",
              message=
              (f"Missing repository_paths[{self._config.name!r}] in EntryConfig"
              ),
          )
      ]

    benchmarks = _read_meta_dict(self._config, "benchmarks")
    metadata = _read_meta_dict(self._config, "eet_benchmark_prep")
    if metadata is None:
      return [
          FailRequirement(
              name="config:eet_benchmark_prep",
              message=
              "Missing/invalid metadata['eet_benchmark_prep'] (expected dict)",
          )
      ]

    scripts_dir_name = metadata.get("scripts_dir")
    required_files = metadata.get("required_files")
    versions = metadata.get("versions")

    if not isinstance(scripts_dir_name, str) or not scripts_dir_name.strip():
      return [
          FailRequirement(
              name="config:scripts_dir",
              message=
              "metadata['eet_benchmark_prep']['scripts_dir'] must be a non-empty string",
          )
      ]
    if not isinstance(benchmarks, list) or not all(
        isinstance(x, str) and x.strip() for x in benchmarks):
      return [
          FailRequirement(
              name="config:benchmarks",
              message=
              "metadata['eet_benchmark_prep']['benchmarks'] must be a list of non-empty strings",
          )
      ]
    if not isinstance(required_files, list) or not all(
        isinstance(x, str) and x.strip() for x in required_files):
      return [
          FailRequirement(
              name="config:required_files",
              message=
              "metadata['eet_benchmark_prep']['required_files'] must be a list of non-empty strings",
          )
      ]
    if not isinstance(versions, dict) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        for k, v in versions.items()):
      return [
          FailRequirement(
              name="config:versions",
              message=
              "metadata['eet_benchmark_prep']['versions'] must be a dict[str,str] with non-empty values",
          )
      ]

    scripts_dir = Path(repo_root / scripts_dir_name)

    # Check repository layout and script directory exist
    _require_path(reqs, name="repo_root", path=repo_root, req_type="dir")
    _require_path(reqs, name="scripts_dir", path=scripts_dir, req_type="dir")

    # Validate ./scripts/ layout
    for bench in benchmarks:
      bench_dir = Path(scripts_dir / bench)

      _require_path(reqs,
                    name=f"scripts_subdir:{bench}",
                    path=bench_dir,
                    req_type="dir")

      for fname in required_files:
        fpath = Path(bench_dir / fname)
        _require_path(reqs,
                      name=f"scripts_file:{bench}:{fname}",
                      path=fpath,
                      req_type="file")

      version = versions.get(bench)
      if not isinstance(version, str) or not version.strip():
        reqs.append(
            FailRequirement(
                name=f"config:version_missing:{bench}",
                message=(
                    "metadata['eet_benchmark_prep']['versions'] missing/invalid "
                    f"for bench {bench!r}"),
            ))
        continue

      run_test = Path(bench_dir / "run_test.sh")
      reqs.append(
          BenchmarkRequirement(
              name=f"run_test_contains_version:{bench}",
              filepath=run_test,
              cmd=("cat", "run_test.sh"),
              signature=version,
              timeout_seconds=10.0,
          ))

    return reqs
