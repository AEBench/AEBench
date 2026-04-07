# Running the Benchmark

## 1. Prerequisites

- Python 3.11+
- `ANTHROPIC_API_KEY` if using the Claude SDK agent (the default)
- Docker, if you want to run cases in Docker mode (most cases use Docker)

Install the package in editable mode from the repo root:
```bash
pip install -e ".[dev]"
```

Verify the CLI works:
```bash
aebench --help
```

## 2. Workspace setup

AEBench discovers its workspace by walking up from the current directory until it finds `aebench.toml` or `cases.json`. So users should run all commands from the repo root (the directory containing `aebench.toml`).

To initialize a fresh workspace:
```bash
aebench init
```

This creates the workspace structure and initializes user config at `~/.config/aebench/config.toml`.

## 3. Running a single case

The simplest way to use the benchmark is evaluating one case at a time:

```bash
# by case ID (looked up in cases.json)
aebench case run eurosys25_egwalker

# by directory path
aebench case run cases/eurosys25_egwalker

# override the model
aebench case run eurosys25_egwalker --model claude-opus-4-6

# keep workspace on disk after the run (useful for debugging)
AEBENCH_PRESERVE_FAILED_WORKSPACE=true aebench case run eurosys25_egwalker
```

What happens under the hood:
1. Clones the artifact source from git (using a local cache)
2. Starts a Docker container (for Docker-mode cases)
3. Launches the agent with the case's README as task instructions
4. Runs the oracle to score the result
5. Prints a summary and writes output files

Output gets written to a timestamped directory under `~/.cache/aebench/case-runs/eurosys25_egwalker/`. The path is printed at the end.

Expected output:
```
Case status: success
Oracle status: success
Score: 4/4
Output dir: /home/user/.cache/aebench/case-runs/eurosys25_egwalker/2026-04-03_12-34-56_000000
```

## 4. Running the full benchmark

To run all registered cases:
```bash
aebench run
```

To run a subset:
```bash
# two specific cases
aebench run eurosys25_egwalker osdi24_anvil

# all cases from a venue
aebench run "cases/eurosys25_*"

# custom output directory
aebench run --output-dir ./my-benchmark-results
```

Cases are run sequentially. When finished, the output directory will contain:
- `benchmark_results.jsonl` — one case result per line
- `benchmark_summary.json` — aggregated stats (pass counts, scores, timings)
- `benchmark_summary.md` — human-readable Markdown table

## 5. Re-summarizing existing results

If you already have per-case outputs and want to regenerate the summary without re-running agents:

```bash
aebench case summarize \
  ~/.cache/aebench/case-runs/eurosys25_egwalker/2026-04-03_12-34-56 \
  ~/.cache/aebench/case-runs/osdi24_anvil/2026-04-03_13-00-00 \
  --output-dir ./my-summary
```

## 6. Running just the oracle

To run only the oracle for a case without re-running the agent:

```bash
aebench case oracle cases/eurosys25_egwalker --output-dir /tmp/oracle-check
```

Useful when developing or debugging oracle logic. The oracle will use the case directory itself as the workspace.

## 7. Exporting cases to JSONL

For custom task pipelines:
```bash
aebench case export eurosys25_egwalker osdi24_anvil --output /tmp/tasks.jsonl
aebench runtime run --input-file /tmp/tasks.jsonl --output-dir /tmp/task-outputs
```

This lower-level interface skips the case-level oracle and writes only `RunResult` records.

## 8. How scoring works

Each case declares an `expected_score` in `case.toml` (usually 4, one per phase). The oracle awards one point for each passing phase:

- **4/4** — full reproduction
- **3/4** — partial; usually means experiment runs failed (the hardest phase)
- **2/4** — build succeeded but benchmark prep failed
- **1/4** — only environment setup passed
- **0/4** — nothing passed (or the agent crashed before oracle ran)

At the benchmark level two aggregate metrics are reported:
- `case_pass_ratio` = cases where all phases passed / total cases
- `phase_ratio` = total points / total possible points (more informative when full reproduction is rare)

By default evaluation uses `failure_mode = "fail_fast"`: if phase N fails, phases N+1 through 4 are marked PENDING. This reflects the real dependency chain — you cant run experiments if the build failed.

## 9. Configuration

### Environment variables

```bash
export AEBENCH_DEFAULT_MODEL=claude-opus-4-6
export AEBENCH_AGENT_KIND=cli
export AEBENCH_DEFAULT_DOCKER_IMAGE=my-registry/my-image:latest
export AEBENCH_PRESERVE_FAILED_WORKSPACE=true
export AEBENCH_EPHEMERAL_WORKSPACE_ROOT=/fast-ssd/workspaces
```

### User config

Create `~/.config/aebench/config.toml`:
```toml
[agent]
agent_type = "claude_sdk"
default_model = "claude-opus-4-6"

[logging]
level = "info"
```

### Workspace config

`aebench.toml` in the project root:
```toml
[agent]
agent_type = "claude_sdk"

[cache.git]
root = "~/.cache/aebench/git"
max_size_bytes = 10_737_418_240  # 10 GB
```

## 10. Debugging tips

**Inspect the workspace after a failed run:**
```bash
export AEBENCH_PRESERVE_FAILED_WORKSPACE=true
aebench case run eurosys25_egwalker
ls /tmp/aebench-workspaces/ae_workspace_eurosys25_egwalker_*/workspace/
```

**Read the agent log:**
```bash
cat ~/.cache/aebench/case-runs/eurosys25_egwalker/2026-04-03_12-34-56/eurosys25_egwalker.log
```

**Re-run just the oracle:**
```bash
aebench case oracle cases/eurosys25_egwalker
```

**Check what prompt was sent to the agent:**
```bash
cat ~/.cache/aebench/case-runs/eurosys25_egwalker/2026-04-03_12-34-56/eurosys25_egwalker_prompt.md
```
