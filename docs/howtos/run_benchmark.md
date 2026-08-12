# Running and Scoring Cases

This checkout supports agent runs for one case and standalone oracle execution. Commands that run multiple cases remain unavailable.

## 1. Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependencies
- Docker for isolated agent runs

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

If a phase fails, the score includes each phase that passed before it. With `failure_mode = "fail_fast"`, later phases remain pending after the first failed phase.

## 5. Run an Agent

On a Chameleon worker, install the project dependencies and build the agent
runtime image:

```bash
uv sync --dev
docker build -t aebench-agent:latest .
```

For a ChatGPT subscription, authenticate Codex on the worker and run the case
with `codex_non_api`:

```bash
codex login
codex login status

uv run aebench case run asplos24_gaia \
  --agent codex_non_api \
  --model gpt-5.5
```

For a Claude subscription, generate a token as described in
[Adding an Agent Harness](add_agent.md#claude-subscription), copy it to the
worker, and run the case with `claude_non_api`:

```bash
export AEBENCH_CLAUDE_OAUTH_TOKEN_FILE=~/.config/aebench/claude_oauth_token

uv run aebench case run osdi24_kondo \
  --agent claude_non_api \
  --model claude-opus-4-8
```

The command prints the run output directory before it starts the agent. It
prints the case status and oracle score after the run finishes.

Use `--allow-host-docker` when `[run.artifact_requirements] docker = true`. This gives the agent control of the host Docker daemon. Use `--allow-unsafe-local` when the case runtime is local. This runs agent commands directly on the host. Use both options only on a disposable worker.

## 6. Unavailable Runner Commands

The CLI accepts these commands but reports that they are unavailable:

```bash
PYTHONPATH=src uv run aebench run
PYTHONPATH=src uv run aebench case export osdi24_anvil --output /tmp/tasks.jsonl
PYTHONPATH=src uv run aebench case summarize /tmp/case-output --output-dir /tmp/summary
PYTHONPATH=src uv run aebench runtime run --input-file /tmp/tasks.jsonl
```

They exit with messages such as:

```text
benchmark runner is unavailable in this checkout
case export is unavailable in this checkout
case summarize is unavailable in this checkout
runtime run is unavailable in this checkout
```

## 7. How Scoring Works

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

## 8. Configuration

### Environment variables

```bash
export AEBENCH_DEFAULT_DOCKER_IMAGE=my-registry/my-image:latest
export AEBENCH_PRESERVE_FAILED_WORKSPACE=true
export AEBENCH_EPHEMERAL_WORKSPACE_ROOT=/fast-ssd/workspaces
```

Select the harness and model explicitly with `--agent` and `--model` for every run.

### User config

Create `~/.config/aebench/config.toml`:

```toml
[logging]
level = "info"
```

### Workspace config

`aebench.toml` in the project root:

```toml
[cache.git]
root = "~/.cache/aebench/git"
max_size_bytes = 10_737_418_240  # 10 GB
```

## 9. Debugging

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
