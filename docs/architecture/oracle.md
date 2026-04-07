# Oracle

The oracle grades the agent's work. After the agent exits, it re-inspects the workspace and runs programmatic checks to score how much of the artifact was successfully reproduced.

## 1. How it works

Each case has an `oracles/` directory with Python modules that define the evaluation logic. The oracle:

1. Discovers concrete oracle classes in that directory at runtime
2. Instantiates each one with a shared `OracleInput` context
3. Calls each class's `report()` method to get a pass/fail result
4. Aggregates phase results into a final `OracleResult` with a numeric score

The oracle runs after the agent has finished. It does not interact with the agent in any way.

## 2. Class hierarchy

```
BaseCheck                  (abstract, in oracles/utils.py)
    └── DependencyVersionCheck
    └── EnvironmentVariableCheck
    └── FilesystemPathCheck
    └── BuildCommandCheck
    └── BenchmarkCommandCheck
    └── ListSimilarityCheck
    └── ... (custom checks for specific cases)

OracleEnvSetupBase         (abstract, in oracles/env_setup_checks.py)
    └── CaseOracleEnvSetupBase     ← inherit from this one
            └── OracleEnvSetup     ← your concrete class

OracleArtifactBuildBase    (abstract)
    └── CaseOracleArtifactBuildBase
            └── OracleArtifactBuild

OracleBenchmarkPrepBase    (abstract)
    └── CaseOracleBenchmarkPrepBase
            └── OracleBenchmarkPrep

OracleExperimentRunsBase   (abstract)
    └── CaseOracleExperimentRunsBase
            └── OracleExperimentRuns
```

The `CaseOracleXxxBase` classes add path helpers (`self.workspace_path(...)`, `self.artifact_path(...)`, etc.) on top of the lower-level phase bases. Always inherit from the `CaseOracleXxxBase` variant.

## 3. OracleInput

Each oracle gets instantiated with an `OracleInput` containing the relevant directories:

- `case_dir` — root of the case bundle (where `case.toml` lives)
- `artifact_dir` — the vendored artifact directory (`case_dir/artifact/`)
- `workspace_dir` — where the agent worked (the temp workspace copy)
- `output_dir` — where run outputs get stored
- `runtime_result` — result of the agent run (can be `None` if oracle runs standalone)

`_CaseOracleBase` stores these as resolved paths and exposes helper methods:

```python
self.workspace_path()              # → workspace_dir
self.workspace_path("src")         # → workspace_dir / "src"
self.artifact_path("data")         # → artifact_dir / "data"
self.ref_path("timings.ref.json")  # → case_dir / "refs" / "timings.ref.json"
self.case_path("oracles")          # → case_dir / "oracles"
self.output_path("results.json")   # → output_dir / "results.json"
```

For backwards compatibility, `self.paths.workspace_dir` also works (used by existing case oracles).

## 4. Phase discovery

The discovery system (`oracles/discovery.py`) imports every `.py` file from the case's `oracles/` directory, finds non-abstract subclasses of the four base classes, and sorts them by priority:

```
ENV_SETUP (100) → ARTIFACT_BUILD (200) → BENCHMARK_PREP (300) → EXPERIMENT_RUNS (400)
```

Some rules:
- Each phase needs exactly one concrete implementation. Duplicates raise `OracleLoadError`.
- The `oracles/` directory must have at least one `.py` file with at least one recognized phase class.
- The case directory is temporarily added to `sys.path` during import so oracle modules can use relative imports (e.g., `from .common import find_repo_root`).

## 5. How a phase runs

For each discovered phase, the execution engine (`oracles/execution.py`):

1. Instantiates the class: `instance = phase_def.cls(context=context, logger=logger)`
2. Calls `instance.report()` which internally runs `build_oracle_report()`
3. `build_oracle_report()` calls `requirements()` to get the check objects, then runs `check.check()` on each one
4. Result is an `OracleReport` containing one `CheckEntry` per check

If `report().ok` is True, the phase passes. If not, it fails. With `failure_mode = "fail_fast"` the remaining phases are marked PENDING and evaluation stops.

## 6. Check classes

A **check** is a frozen dataclass inheriting from `BaseCheck`. It represents one specific thing to verify. You declare checks in `requirements()` and the base class runs them automatically.

Every check has:
- `name` — unique identifier within the phase (e.g., `"rustc"`, `"dataset_file_exists"`)
- `optional` — if True, failure is a warning not an error

The `check()` method returns a `CheckResult`:
- `ok` — whether the check passed
- `message` — human-readable description
- `stdout`, `stderr` — captured output (for command-based checks)
- `returncode` — process exit code
- `timed_out` — whether a timeout was hit

### Built-in check types

**Environment setup (`env_setup_checks.py`):**
- `DependencyVersionCheck` — runs a command (like `rustc --version`), parses the version, fails if not meeting the required version
- `EnvironmentVariableCheck` — checks an env var is set and matches (exact, contains, or regex)
- `FilesystemPathCheck` — checks a path exists and optionally that its a file or directory

**Artifact build (`artifact_build_checks.py`):**
- `BuildCommandCheck` — runs a build command with a timeout, fails on non-zero exit. Streams output to a bounded buffer to avoid running out of memory on large builds.

**Benchmark prep (`benchmark_prep_checks.py`):**
- `BenchmarkCommandCheck` — runs a setup command and optionally checks output against a signature string
- `BenchmarkCheck` — backward-compatible wrapper that combines path check + command check

**Experiment runs (`experiment_runs_checks.py`):**
- `ListSimilarityCheck` — compares observed and reference float sequences using Pearson correlation (or other metrics); fails if below a threshold
- `ElementwiseEqualityCheck` — compares sequences element-by-element
- `ElementwiseSimilarityThresholdCheck` — element-wise comparison with per-element tolerance

## 7. Scoring

The oracle score is simply the number of phases that passed:

- Each passing phase = 1 point
- `CaseConfig.oracle.expected_score` declares the maximum (usually 4)
- At the benchmark level, `phase_ratio` = total points / total possible points

A case's status will be:
- `SUCCESS` — all phases passed
- `ERROR` — at least one phase failed or the runtime crashed
- `INTERRUPTED` — agent was interrupted before the oracle ran
- `PENDING` — oracle was not reached (runtime failed)

## 8. Running the oracle standalone

Users can run just the oracle for a case without re-running the agent:

```bash
aebench case oracle cases/eurosys25_egwalker
```

This is useful for iterating on oracle logic while keeping the agent's workspace output intact.

## 9. The `common.py` pattern

Oracle modules for a case often share helper code. The convention is to put shared utilities in `oracles/common.py` and import from each phase module:

```python
# oracles/common.py
from pathlib import Path

def find_repo_root(workspace_dir: Path) -> Path | None:
    for candidate in workspace_dir.iterdir():
        if (candidate / ".git").is_dir():
            return candidate
    return None
```

```python
# oracles/env_setup.py
from .common import find_repo_root
```

`common.py` is not discovered as a phase because it contains no oracle subclass.
