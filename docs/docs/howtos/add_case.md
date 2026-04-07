# Adding a Case

How to add a new research artifact to AEBench. By the end you will have a case bundle with `case.toml`, reference data in `refs/`, and oracle code for all four phases.

## 1. What is a case bundle?

A case bundle is a self-contained directory with everything AEBench needs to evaluate an agent against one research artifact:

```plaintext
cases/venue24_paperid/
├── case.toml              # manifest: identity, source, runtime, oracle
├── refs/                  # reference outputs the oracle checks against
│   ├── datasets.ref.json
│   └── results.ref.json
└── oracles/               # evaluation logic, one file per phase
    ├── common.py          # shared helpers (not a phase itself)
    ├── env_setup.py
    ├── artifact_build.py
    ├── benchmark_prep.py
    └── experiment_runs.py
```

> [!NOTE]
> The `artifact/` subdirectory is optional. It holds a vendored copy of the artifact when `artifact_mode = "vendor"`. For most cases the artifact is fetched from git at run time so you dont need it.

## 2. Scaffold the bundle

Use the `case init` command to create a starter bundle from a git repo:

```bash
aebench case init https://github.com/author/paper-repo.git \
  --id venue24_paperid \
  --ref abc123def456 \
  --target-dir cases/venue24_paperid
```

- `--id` — the case identifier. Convention: `<venue><year>_<shortname>` (e.g., `osdi24_anvil`)
- `--ref` — the git commit hash or tag to pin. Always use a full commit hash for reproducibility
- `--target-dir` — where to create the bundle. Defaults to a `bundles/` subdirectory
- `--blank` — create an empty bundle with placeholder files instead of scaffolding from source

After the command completes it will prompt you (in interactive terminals) to fill in the case brief. Users can also create the directory and write `case.toml` by hand instead.

## 3. Write `case.toml`

This is the manifest file. Here is a complete example:

```toml
id = "eurosys25_egwalker"

[case_brief]
core_claim = "Build eg-walker, stage the referenced datasets, and reproduce the benchmark timing outputs."
acceptable_evidence = "Build artifacts present, datasets at expected sizes, timings.json with Pearson correlation >= 0.75 against reference."
allowed_tolerance = "Timing values may vary across environments but must preserve the reference trend."

[run]
id = "eurosys25_egwalker"

[run.instructions]
path = "README.md"

[run.runtime]
mode = "docker"
timeout_ms = 345600000      # 4 days — long builds need long timeouts
gpu = false
interactive = false

[run.prompt]
profile = "artifact-eval-v1"

[oracle]
expected_score = 4
phases = ["env_setup", "artifact_build", "benchmark_prep", "experiment_runs"]
score_mode = "phase_count"
failure_mode = "fail_fast"

[upstream]
source_type = "git"
url = "https://github.com/josephg/egwalker-paper.git"
ref = "4d9bef55e4f2e3b3b8b0efe8f91cd35d34ed35a8"
```

Key choices to make:
- **`runtime.mode`**: use `"docker"` if the artifact has complex deps or needs isolation. Use `"local"` only for simple cases safe to run on the host
- **`runtime.timeout_ms`**: set this generously. Large builds and dataset downloads can take hours. 4 hours (`14_400_000`) is reasonable for most cases
- **`upstream.ref`**: always pin to a full commit hash. Branch names change over time and break reproducibility

## 4. Register the case

Add an entry to `cases.json`:

```json
{
  "schema_version": 1,
  "cases_dir": "cases",
  "cases": {
    "eurosys25_egwalker": {"path": "cases/eurosys25_egwalker"},
    "venue24_paperid":    {"path": "cases/venue24_paperid"}
  }
}
```

After this you can refer to the case by ID anywhere the CLI accepts a case reference.

## 5. Prepare reference data

The `refs/` directory holds ground truth that the oracle checks against. What goes in here depends on the case, but common patterns are:
- Dataset manifests with expected file sizes or checksums
- Expected experiment outputs (JSON with numeric results from a correct run)
- Signature strings expected in command output

Run the experiment yourself on the artifact and save the outputs as reference files:

```bash
python run_experiment.py > results.json
cp results.json cases/venue24_paperid/refs/results.ref.json
```

Commit these to the repo. They travel with the case bundle.

## 6. Implement the oracle

The oracle lives in `oracles/`. Each file defines one phase class. Discovery is automatic based on which base class they inherit from.

### Base classes

Always inherit from the `CaseOracleXxxBase` classes (not the lower-level ones). These inject the context, paths, and path helper methods.

| File | Inherit from |
|---|---|
| `oracles/env_setup.py` | `CaseOracleEnvSetupBase` |
| `oracles/artifact_build.py` | `CaseOracleArtifactBuildBase` |
| `oracles/benchmark_prep.py` | `CaseOracleBenchmarkPrepBase` |
| `oracles/experiment_runs.py` | `CaseOracleExperimentRunsBase` |

### Path helpers

Inside any oracle class:
```python
self.workspace_path()              # where the agent worked
self.workspace_path("src", "app")  # workspace/src/app
self.artifact_path("data")         # vendored artifact dir / data
self.ref_path("results.ref.json")  # cases/venue24_paperid/refs/results.ref.json
self.output_path("oracle_out.json")
```

### Shared helpers — `oracles/common.py`

Put code shared across phases in `common.py`. It gets ignored during phase discovery (no oracle base class), but phase modules can import from it:

```python
# oracles/common.py
from pathlib import Path

def find_repo_root(workspace_dir: Path) -> Path | None:
    for entry in workspace_dir.iterdir():
        if entry.is_dir() and (entry / ".git").is_dir():
            return entry
    return None
```

### Phase 1 — env_setup

Check that required tools are installed at the right versions:

```python
from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleEnvSetupBase
from evaluator.oracles.env_setup_checks import (
    DependencyVersionCheck,
    FilesystemPathCheck,
    PathType,
    VersionCompare,
)
from .common import RepoRootLocatedCheck, find_repo_root


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = find_repo_root(self.paths.workspace_dir)
        if repo_root is None:
            return (RepoRootLocatedCheck(
                name="repo_root_located",
                workspace_dir=self.paths.workspace_dir,
            ),)

        return (
            DependencyVersionCheck(
                name="python",
                cmd=("python3", "--version"),
                required_version=(3, 10, 0),
                compare=VersionCompare.GEQ,
            ),
            FilesystemPathCheck(
                name="repo_root_exists",
                path=repo_root,
                path_type=PathType.DIRECTORY,
            ),
        )
```

### Phase 2 — artifact_build

Verify the build succeeded. The recommeded pattern is to check for expected output files (the "verify" mode), with an optional "command" mode that re-runs the build:

```python
from __future__ import annotations
import os
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleArtifactBuildBase
from evaluator.oracles.artifact_build_checks import BuildCommandCheck
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from .common import RepoRootLocatedCheck, find_repo_root

_EXPECTED_OUTPUTS = ("build/my-tool", "build/lib/my-lib.so")
_BUILD_MODE_ENV = "AE_MYPAPER_BUILD_MODE"


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = find_repo_root(self.paths.workspace_dir)
        if repo_root is None:
            return (RepoRootLocatedCheck(
                name="repo_root_located",
                workspace_dir=self.paths.workspace_dir,
            ),)

        mode = (os.environ.get(_BUILD_MODE_ENV, "verify") or "verify").strip().lower()

        if mode == "command":
            return (BuildCommandCheck(
                name="build_artifact",
                cwd=repo_root,
                cmd=("make", "-j8", "all"),
                timeout_seconds=3600.0,
            ),)

        return tuple(
            FilesystemPathCheck(
                name=f"output_{Path(rel).name}",
                path=repo_root / rel,
                path_type=PathType.FILE,
            )
            for rel in _EXPECTED_OUTPUTS
        )
```

### Phase 3 — benchmark_prep

Check that datasets were downloaded and any prep tools built:

```python
from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleBenchmarkPrepBase
from evaluator.oracles.env_setup_checks import FilesystemPathCheck, PathType
from .common import RepoRootLocatedCheck, find_repo_root


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = find_repo_root(self.paths.workspace_dir)
        if repo_root is None:
            return (RepoRootLocatedCheck(
                name="repo_root_located",
                workspace_dir=self.paths.workspace_dir,
            ),)

        return (
            FilesystemPathCheck(
                name="dataset_dir",
                path=repo_root / "data" / "my-dataset",
                path_type=PathType.DIRECTORY,
            ),
            FilesystemPathCheck(
                name="dataset_file",
                path=repo_root / "data" / "my-dataset" / "train.csv",
                path_type=PathType.FILE,
            ),
        )
```

### Phase 4 — experiment_runs

Compare the agent's results against reference values. The key check class is `ListSimilarityCheck`, which compares two float sequences using Pearson correlation (or other metric):

```python
from __future__ import annotations
import json
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import utils
from evaluator.oracles.case_base import CaseOracleExperimentRunsBase
from evaluator.oracles.experiment_runs_checks import (
    ListSimilarityCheck,
    SimilarityMetric,
)
from .common import RepoRootLocatedCheck, find_repo_root


def _load_values(path: Path) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [float(v) for variants in data.values() for v in variants.values()]


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        repo_root = find_repo_root(self.paths.workspace_dir)
        if repo_root is None:
            return (RepoRootLocatedCheck(
                name="repo_root_located",
                workspace_dir=self.paths.workspace_dir,
            ),)

        observed = _load_values(repo_root / "outputs" / "results.json")
        reference = _load_values(self.ref_path("results.ref.json"))

        return (
            ListSimilarityCheck(
                name="results_correlation",
                observed=observed,
                reference=reference,
                metric=SimilarityMetric.PEARSON,
                min_similarity=0.75,
            ),
        )
```

> [!NOTE]
> 0.75 Pearson correlation is a reasonable starting point for performance benchmarks. For exact outputs (e.g., classification accuracy), use `ElementwiseEqualityCheck` instead.

## 7. Test the oracle standalone

Before running the full pipeline, test the oracle by itself:

```bash
aebench case oracle cases/venue24_paperid
```

This runs all four phases against the case directory (using it as the workspace). If a phase fails, add print statements or logging in your oracle code and re-run.

## 8. Run the full case

Once the oracle works standalone, run the full pipeline:

```bash
AEBENCH_PRESERVE_FAILED_WORKSPACE=true aebench case run venue24_paperid
```

Check the output files:
- `case_result.json` — full result with per-phase oracle outcomes
- `<case_id>_report.md` — human-readable summary
- `<case_id>.log` — captured agent output

## 9. Best practices

- Keep `requirements()` deterministic. Given the same workspace state it must always return the same checks
- Make checks idempotent. The oracle should only *read* the workspace, never modify it
- Set realistic timeouts on `BuildCommandCheck`. Build times vary significantly across machines
- Use `optional=True` for nice-to-have checks that shouldnt block the phase
- Write descriptive `name` strings — they show up in the oracle report. `"rustc_version"` is better than `"check1"`
- Handle missing workspace gracefully. The repo root might not exist if the agent failed before cloning. Return a single `RepoRootLocatedCheck` in that case rather than letting the oracle crash
