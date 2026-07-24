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
> The `artifact/` subdirectory is optional. It holds a vendored copy of the artifact when `artifact_mode = "vendor"`. Most audit work points `aebench case oracle` at a separately prepared workspace with `--workspace-dir`.

## 2. Scaffold the bundle

Use the `case init` command to create a starter bundle:

```bash
PYTHONPATH=src uv run aebench case init --blank \
  --id venue24_paperid \
  --target-dir cases/venue24_paperid
```

- `--id` — the case identifier. Convention: `<venue><year>_<shortname>` (e.g., `osdi24_anvil`)
- `--target-dir` — where to create the bundle. Defaults to a `bundles/` subdirectory
- `--blank` — create an empty bundle with placeholder files instead of scaffolding from source
- `--ref` — accepted by the CLI for source-style initialization, but you should still verify and fill in the generated `[upstream]` metadata by hand

The current scaffold writes template files. You can also create the directory and write `case.toml` by hand.

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
required_evidence = [
  "Save the benchmark stdout to results/table1.txt.",
  "Keep the full experiment log at logs/experiment.log.",
  "Leave generated result files in results/ for the oracle to inspect.",
]

[run.instructions]
path = "README.md"

[run.runtime]
mode = "docker"
image = "aebench-agent:latest"
timeout_ms = 345600000
gpu = false
interactive = false
commit_before_oracle = true
keep_committed_snapshot = false
snapshot_timeout_seconds = 60.0

[run.artifact_requirements]
docker = true
compose = false

[run.prompt]
profile = "artifact-eval-v1"

[oracle]
expected_score = 4
phases = ["env_setup", "artifact_build", "benchmark_prep", "experiment_runs"]
score_mode = "phase_count"
failure_mode = "fail_fast"

[oracle.runtime]
mode = "local"

[upstream]
source_type = "git"
url = "https://github.com/josephg/egwalker-paper.git"
ref = "4d9bef55e4f2e3b3b8b0efe8f91cd35d34ed35a8"
artifact_mode = "hybrid"
overlay_artifact = true

[paper]
url = "https://example.com/paper.pdf"
sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
title = "Artifact paper title"
```

Key choices to make:
- **`runtime.mode`**: use `"docker"` if the artifact has complex deps or needs isolation. Use `"local"` only for simple cases safe to run on the host
- **`runtime.timeout_ms`**: set this generously. Large builds and dataset downloads can take hours. 4 hours (`14_400_000`) is reasonable for most cases
- **`required_evidence`**: list the exact logs, redirected stdout files, tables, or result artifacts the agent must leave in the workspace for the oracle to inspect
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

from evaluator.oracles import CaseOracleEnvSetupBase, PathKind, utils


class OracleEnvSetup(CaseOracleEnvSetupBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            self.version_check(
                name="python3_version",
                cmd=("python3", "--version"),
                min_version=(3, 10, 0),
            ),
            self.path_check(
                name="instructions_exist",
                path=self.workspace_path("README.md"),
                kind=PathKind.FILE,
            ),
        )
```

### Phase 2 — artifact_build

Verify the build succeeded. The recommended pattern is to check for expected output files (the "verify" mode), with an optional "command" mode that re-runs the build:

```python
from __future__ import annotations
import os
from collections.abc import Sequence
from pathlib import Path

from evaluator.oracles import CaseOracleArtifactBuildBase, PathKind, utils

_EXPECTED_OUTPUTS = ("build/my-tool", "build/lib/my-lib.so")
_BUILD_MODE_ENV = "AE_MYPAPER_BUILD_MODE"


class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        mode = (os.environ.get(_BUILD_MODE_ENV, "verify") or "verify").strip().lower()

        if mode == "command":
            return (self.command_check(
                name="build_artifact",
                cwd=self.workspace_path(),
                cmd=("make", "-j8", "all"),
                timeout_seconds=3600.0,
            ),)

        return tuple(
            self.path_check(
                name=f"output_{Path(rel).name}",
                path=self.workspace_path(rel),
                kind=PathKind.FILE,
            )
            for rel in _EXPECTED_OUTPUTS
        )
```

### Phase 3 — benchmark_prep

Check that datasets were downloaded and any prep tools built:

```python
from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles import CaseOracleBenchmarkPrepBase, PathKind, utils


class OracleBenchmarkPrep(CaseOracleBenchmarkPrepBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        return (
            self.path_check(
                name="dataset_dir",
                path=self.workspace_path("data", "my-dataset"),
                kind=PathKind.DIRECTORY,
            ),
            self.path_check(
                name="dataset_file",
                path=self.workspace_path("data", "my-dataset", "train.csv"),
                kind=PathKind.FILE,
            ),
            self.directory_glob_count_check(
                name="prepared_result_count",
                directory=self.workspace_path("outputs"),
                pattern="*.json",
                min_count=1,
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

from evaluator.oracles import (
    CaseOracleExperimentRunsBase,
    ListSimilarityCheck,
    SimilarityMetric,
    utils,
)


def _load_values(path: Path) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [float(v) for variants in data.values() for v in variants.values()]


class OracleExperimentRuns(CaseOracleExperimentRunsBase):
    def requirements(self) -> Sequence[utils.BaseCheck]:
        observed = _load_values(self.workspace_path("outputs", "results.json"))
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
PYTHONPATH=src uv run aebench case oracle cases/venue24_paperid \
  --workspace-dir /path/to/prepared/artifact \
  --output-dir /tmp/aebench-venue24-paperid-oracle
```

This runs all four phases against the prepared artifact workspace. If `--workspace-dir` is omitted, the case directory itself is used as the workspace.

## 8. Run the full case

The full agent pipeline is currently unavailable in this checkout:

```bash
PYTHONPATH=src uv run aebench case run venue24_paperid
```

That command exits with `case runner is unavailable in this checkout`. Audit cases by manually preparing the artifact workspace and then running `aebench case oracle`.

## 9. Best practices

- Keep `requirements()` deterministic. Given the same workspace state it must always return the same checks
- Make checks idempotent. The oracle should only *read* the workspace, never modify it
- Set realistic timeouts on `command_check`. Build times vary significantly across machines
- Use `optional=True` for nice-to-have checks that should not block the phase
- Write descriptive `name` strings — they show up in the oracle report. `"rustc_version"` is better than `"check1"`
- Handle missing workspace gracefully. Prefer a clear failing `path_check` or custom `utils.Check` result rather than letting the oracle crash
