# AEBench

AEBench is a benchmark for evaluating AI agents on Artifact Evaluation (AE) tasks. It packages official AE cases as versioned cases, runs them through a shared runtime, and records benchmark-level results in a reproducible way. For context on why this benchmark exists, see [WHY.md](WHY.md).

## Overview

The current repository is organized around three layers:

- `cases.json`: the versioned catalog of official case ids
- `cases/<case_id>/`: case content for each official case
- `src/`: the runtime, CLI, reporting, and Docker execution logic

Each case contains the artifact instructions, oracle entrypoint, and reference data needed to score a case. In this checkout, case authoring, case validation, and standalone oracle execution are the active CLI workflows. The full agent runner, benchmark runner, JSONL export, JSONL runtime, and summary regeneration commands are present in the parser but intentionally unavailable.

## Quick Start

Requirements:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Docker only when manually reproducing artifacts or developing future Docker-mode runs

Install the project:

```bash
git clone https://github.com/AEBench/AEBench.git
cd AEBench

uv sync --dev
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

The following workflows are not available in this checkout even though their subcommands appear in `--help`:

- `aebench run`
- `aebench case run`
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

`aebench case run`, `aebench run`, and `aebench case export` currently raise "unavailable in this checkout".

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

## Agent Drivers

The runtime currently supports built-in drivers for Claude SDK, mock, Python,
CLI, remote, and MCP-capable clients.

Adding or modifying an agent driver is a code-level integration inside `src/`.

## Adding Or Modifying An Agent Driver

Agent integrations plug into a single runtime path:

`TaskRunner -> RunSession -> Agent -> RuntimeBackend`

If you only want to add a new benchmark case, you usually do not need to touch the driver layer.

### Current Drivers

The runtime currently exposes these driver kinds:

- `claude_sdk`: the default production driver
- `mock`: a test-only driver for smoke and offline workflow checks
- `python`: load a driver factory from Python
- `cli`: launch an external CLI through the runtime
- `remote`: call an external HTTP endpoint
- `mcp_client`: launch an MCP-capable client

Driver selection is resolved from project and user configuration and exposed as `[agent]` configuration. In this repo, `[agent]` means "agent driver configuration".

### Architecture

The runtime is built around four classes:

- `AppState`
  Top-level context. Holds project config and all runtime settings. Every runner receives one.
- `TaskRunner`
  Creates the workspace, builds the prompt, starts the backend and agent, collects results.
- `RunSession`
  Frozen dataclass carrying per-run state (workspace paths, prompt, runtime backend handle) shared between backend and agent.
- `Agent`
  Implements `prepare()`, `execute()`, and `cleanup()` for a specific driver kind.
- `RuntimeBackend`
  Implements `prepare()`, `collect_artifacts()`, `cleanup()`, and `runtime_result()` for `local` and `docker` runtimes.

### How To Add A Driver

1. Add the new type to the `AgentType` enum in `src/settings.py`.

2. Implement the `Agent` protocol in `src/runtime/driver.py`:

```python
class MyAgent:
    name: str = "my_agent"

    def prepare(self, session, listener=None) -> None:
        pass

    def execute(self, request: AgentRequest, session, listener=None) -> AgentResult:
        # session.host_workspace: workspace path on host
        # session.runtime_workspace: workspace path inside the container
        # request.system_prompt, request.initial_prompt: prompts
        # request.timeout_ms: timeout
        ...
        return AgentResult(
            model=request.model,
            exit_code=0,
            output="Agent summary here",
            message_count=42,
        )

    def cleanup(self, session, listener=None) -> None:
        pass
```

3. Register it in `get_agent()` in `src/runtime/driver.py`:

```python
if agent_type == AgentType.MY_AGENT:
    return MyAgent()
```

4. Update config loading in `src/settings.py` and `src/project_config.py` if the new driver needs config or environment inputs.

### Tests To Add

At minimum, update or add:

- coverage for `get_agent()` with the new driver type
- smoke coverage if the driver can run offline

### Practical Advice

- Prefer drivers over ad hoc wrappers or one-off scripts.
- Keep driver-specific config shaping in one place.
- Do not introduce driver-specific assumptions into case bundles.
- If a driver is test-only, keep it off the production default path.
- See [docs/howtos/add_agent.md](docs/howtos/add_agent.md) for the full walkthrough, including the CLI and Python agent options that may save you from writing a custom class at all.


## Runtime Backends

Most new integrations only need a driver. Add or modify a `RuntimeBackend` only if the runtime itself changes.

- `LocalRuntime`
  Runs commands directly on the host. Used when `runtime.mode = "local"`.
- `DockerRuntime`
  Owns container lifecycle, workspace mounting, and artifact collection. Used when `runtime.mode = "docker"`.

If a driver needs different container or host behavior, express it through the runtime backend and keep the driver itself environment-agnostic.

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
