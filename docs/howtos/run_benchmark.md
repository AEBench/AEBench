# Running and Scoring Cases

This checkout currently supports case authoring, validation, and standalone oracle execution. The full agent runner and benchmark runner subcommands are still present in `--help`, but they intentionally raise "unavailable in this checkout".

## 1. Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependencies
- Docker only when the upstream artifact itself requires Docker

Install dependencies from the repo root:

```bash
uv sync --dev
```

Run CLI commands with `src` on `PYTHONPATH`:

```bash
PYTHONPATH=src uv run aebench --help
PYTHONPATH=src uv run aebench case --help
```

## 2. Workspace Setup

AEBench discovers its workspace by walking up from the current directory until it finds `aebench.toml` or `cases.json`. Run commands from the repo root unless you intentionally want another workspace root.

Initialize a fresh workspace:

```bash
PYTHONPATH=src uv run aebench init
```

This creates the workspace structure and initializes user config at `~/.config/aebench/config.toml`.

## 3. Validate a Case

Validate the case bundle before running or debugging any oracle logic:

```bash
PYTHONPATH=src uv run aebench case validate osdi24_anvil
PYTHONPATH=src uv run aebench case validate cases/osdi24_anvil
```

Expected successful output:

```text
Case bundle is valid: /path/to/AEBench/cases/osdi24_anvil
```

## 4. Run Just the Oracle

To score an already prepared artifact workspace without running an agent:

```bash
PYTHONPATH=src uv run aebench case oracle osdi24_anvil \
  --workspace-dir /path/to/artifact/workspace \
  --output-dir /tmp/aebench-osdi24-anvil-oracle
```

`--workspace-dir` should point at the directory containing the files produced by the artifact setup, build, preparation, and experiments. If omitted, the oracle uses the case directory as the workspace, which is useful only for simple smoke checks.

Expected output shape:

```text
Oracle status: success
Score: 4/4
```

If a phase fails, the score reflects how far the artifact got. With `failure_mode = "fail_fast"`, later phases are marked pending after the first failed phase.

## 5. Unavailable Runner Commands

These commands are parsed but currently unavailable:

```bash
PYTHONPATH=src uv run aebench run
PYTHONPATH=src uv run aebench case run osdi24_anvil
PYTHONPATH=src uv run aebench case export osdi24_anvil --output /tmp/tasks.jsonl
PYTHONPATH=src uv run aebench case summarize /tmp/case-output --output-dir /tmp/summary
PYTHONPATH=src uv run aebench runtime run --input-file /tmp/tasks.jsonl
```

They exit with messages such as:

```text
benchmark runner is unavailable in this checkout
case runner is unavailable in this checkout
case export is unavailable in this checkout
case summarize is unavailable in this checkout
runtime run is unavailable in this checkout
```

Use the standalone oracle command while auditing cases manually.

## 6. How Scoring Works

Each case declares an `expected_score` in `case.toml`, usually `4`, one point per phase:

- **4/4**: full reproduction
- **3/4**: partial; usually means experiment runs failed
- **2/4**: build succeeded but benchmark prep failed
- **1/4**: only environment setup passed
- **0/4**: no phase passed

The four standard phases are:

- `env_setup`
- `artifact_build`
- `benchmark_prep`
- `experiment_runs`

## 7. Configuration

### Environment variables

```bash
export AEBENCH_DEFAULT_MODEL=claude-opus-4-6
export AEBENCH_AGENT_KIND=cli
export AEBENCH_DEFAULT_DOCKER_IMAGE=my-registry/my-image:latest
export AEBENCH_PRESERVE_FAILED_WORKSPACE=true
export AEBENCH_EPHEMERAL_WORKSPACE_ROOT=/fast-ssd/workspaces
```

These variables are still part of the runtime configuration model, but they matter only when using code paths that actually launch agents or Docker runtimes.

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

## 8. Debugging Tips

**Validate the case bundle first:**

```bash
PYTHONPATH=src uv run aebench case validate osdi24_anvil
```

**Run the oracle with an explicit artifact workspace:**

```bash
PYTHONPATH=src uv run aebench case oracle osdi24_anvil \
  --workspace-dir /path/to/artifact/workspace \
  --output-dir /tmp/aebench-oracle-check
```

**Inspect the oracle output directory:**

```bash
find /tmp/aebench-oracle-check -maxdepth 2 -type f -print
```
