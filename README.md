# AEBench

AEBench is a benchmark for evaluating AI agents on Artifact Evaluation (AE) tasks. It packages official AE cases as versioned cases, runs them through a shared runtime, and records benchmark-level results in a reproducible way. For context on why this benchmark exists, see [WHY.md](WHY.md).

## Overview

The current repository is organized around three layers:

- `cases.json`: the versioned catalog of official case ids
- `cases/<case_id>/`: case content for each official case
- `src/`: the runtime, CLI, reporting, and Docker execution logic

Each case identifies the artifact instructions and contains an oracle entrypoint and reference data. A Codex or Claude Code harness can run one case, after which the AEBench oracle scores the result. Commands that run multiple cases or use the JSONL runtime remain unavailable.

## Quick Start

Requirements:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Docker for isolated agent runs

Install the project:

```bash
git clone https://github.com/AEBench/AEBench.git
cd AEBench

uv sync --dev
docker build -t aebench-agent:latest .
```

Run the CLI through `uv` with `src` on `PYTHONPATH`:

```bash
PYTHONPATH=src uv run aebench --help
PYTHONPATH=src uv run aebench case --help
```

## Current Case Workflows

Initialize a workspace:

```bash
PYTHONPATH=src uv run aebench init
```

Create or template a case bundle:

```bash
PYTHONPATH=src uv run aebench case init --blank --id my-case --target-dir cases/my-case
PYTHONPATH=src uv run aebench case template cases/my-case
```

Validate a registered case:

```bash
PYTHONPATH=src uv run aebench case validate osdi24_anvil
```

Run only the oracle against an existing artifact workspace:

```bash
PYTHONPATH=src uv run aebench case oracle osdi24_anvil \
  --workspace-dir /path/to/artifact/workspace \
  --output-dir /tmp/aebench-osdi24-anvil-oracle
```

Standalone oracle runs are the main way to audit a case after manually building and running the upstream artifact.

Run one case with ChatGPT subscription authentication:

```bash
uv run aebench case run osdi24_kondo \
  --agent codex_non_api \
  --model gpt-5.3-codex
```

Run one case with Claude subscription authentication after generating a token with `claude setup-token`:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...
uv run aebench case run osdi24_kondo \
  --agent claude_non_api \
  --model claude-opus-4-6
```

Cases that use the host Docker daemon also require `--allow-host-docker`. Use that option only on a disposable worker.

The following workflows are not available in this checkout even though their subcommands appear in `--help`:

- `aebench run`
- `aebench case export`
- `aebench case summarize`
- `aebench runtime run`

For the current oracle workflow and the unavailable runner commands, see [docs/howtos/run_benchmark.md](docs/howtos/run_benchmark.md).


## Adding a New Artifact or "Cases"

New benchmark cases are added as cases rather than through the legacy benchmark-workspace layout.

Common entrypoints:

```bash
PYTHONPATH=src uv run aebench init
PYTHONPATH=src uv run aebench case init --blank --id my-case --target-dir cases/my-case
PYTHONPATH=src uv run aebench case template cases/my-case
PYTHONPATH=src uv run aebench case validate cases/my-case
```

For the first-time authoring walkthrough, including `case.toml` fields, registry behavior, and oracle implementation, see [docs/howtos/add_case.md](docs/howtos/add_case.md).

### Authoring Entry Points

Initialize a workspace:

```bash
PYTHONPATH=src uv run aebench init
```

Create a new empty case:

```bash
PYTHONPATH=src uv run aebench case init --blank --id my-case --target-dir cases/my-case
```

Create a starter case from a source-like identifier:

```bash
PYTHONPATH=src uv run aebench case init ./path/to/artifact --id my-case --target-dir cases/my-case
PYTHONPATH=src uv run aebench case init https://github.com/org/repo.git --id my-case --ref main --target-dir cases/my-case
```

The current scaffold writes local template files. Fill in the real `case.toml` upstream metadata and oracle logic before submitting a case.

### Common Commands

Validate a case and run its oracle:

```bash
PYTHONPATH=src uv run aebench case validate cases/my-case
PYTHONPATH=src uv run aebench case oracle cases/my-case \
  --workspace-dir /path/to/artifact/workspace \
  --output-dir /tmp/aebench-my-case-oracle
```

`aebench run` and `aebench case export` currently raise "unavailable in this checkout".

### `case.toml`

Minimal example:

```toml
id = "demo-case"

[case_brief]
core_claim = "Summarize the clean-baseline claim this case should validate."
acceptable_evidence = "Describe what should count as success for this case."
allowed_tolerance = "n/a"

[run]
id = "demo-case"

[run.instructions]
path = "README.md"

[run.runtime]
mode = "docker"
timeout_ms = 120000
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
url = "https://github.com/org/repo.git"
ref = "deadbeef..."
```

## Agent Harnesses

`aebench case run` supports Codex and Claude Code through four shell harnesses:

- `codex`
- `codex_non_api`
- `claude`
- `claude_non_api`

Select the harness and model with `--agent` and `--model`. AEBench does not read agent selection from project or user configuration. See [docs/howtos/add_agent.md](docs/howtos/add_agent.md) for authentication and extension details.


## Runtime Backends

Add or modify a runtime backend only when command execution or isolation requirements change.

- `LocalRuntime`
  Runs commands directly on the host. Used when `runtime.mode = "local"`.
- `DockerRuntime`
  Manages the container, workspace mounts, and snapshots. Used when `runtime.mode = "docker"`.

Shell harnesses use the selected backend to run commands. Keep runtime-specific operations in the backend.

## Development and Testing

Useful commands:

```bash
PYTHONPATH=src uv run python -m pytest tests/unit tests/functional
PYTHONPATH=src uv run python -m pytest tests/unit/
PYTHONPATH=src uv run python -m pytest tests/functional/
PYTHONPATH=src uv run python -m pytest tests/integration/
PYTHONPATH=src uv run python -m pytest -m sanity
PYTHONPATH=src uv run python -m pytest --collect-only -q
uv run ruff check src tests
uv run ruff format --check src tests
```
