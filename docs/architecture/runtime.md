# Runtime

How AEBench prepares a workspace, launches an agent, and records the result.

## 1. Main classes

### AppState

`AppState` is the top-level context object. It holds two things:
- `project_state`: the resolved project configuration (which cases exist, where outputs go, etc.)
- `settings`: all runtime parameters (model, agent type, timeouts, docker image, etc.)

Every runner (`BenchmarkRunner`, `CaseRunner`) receives an `AppState`. The CLI creates one at the start of each command via `_build_context()`.

### BenchmarkRunner

Runs multiple cases one after another. For each case it delegates to `CaseRunner.run()`, collects results, and writes a benchmark summary (JSON + Markdown) to the output directory.

### CaseRunner

Handles a single case end-to-end. Its `run()` method:

1. Loads `case.toml` into a `CaseConfig`
2. Resolves the output directory (timestamped under `~/.cache/aebench/case-runs/<case-id>/`)
3. Calls `TaskRunner.run()` to run the agent
4. If the task succeeded, calls `OracleRunner.execute()` to score the result
5. Writes `case_result.json`

### TaskRunner

Handles the lower-level details of running one agent task:

1. Creates a temp workspace directory under `AEBENCH_EPHEMERAL_WORKSPACE_ROOT`
2. Copies or clones the artifact source into the workspace
3. Reads the instruction file
4. Builds the full prompt (system + initial prompt) via Jinja2 templates
5. Starts the runtime backend (Docker container or local)
6. Starts the agent
7. Calls `Agent.execute()`: the agent does its work here
8. Collects artifacts and cleans up in a `finally` block
9. Returns a `RunResult`

### RunSession

A frozen dataclass holding all per-task state that gets shared between the runtime backend and the agent. Contains workspace paths, config, prompt, runtime backend handle, etc. Created once by `TaskRunner` and passed around to prepare/execute/cleanup calls.

## 2. Runtime backends

A backend controls where and how commands run. Selected by `runtime.mode` in `case.toml`.

**LocalRuntime**: runs commands directly on the host. The workspace directory is used as-is, no containers. Used when `runtime.mode = "local"`.

**DockerRuntime**: starts a Docker container with the workspace mounted at `/repo` and refs at `/refs:ro`. The agent interacts with the container. Container is force-removed on cleanup.

Key Docker parameters from `case.toml` or settings:
- `runtime.image`: Docker image. Falls back to `AEBENCH_DEFAULT_DOCKER_IMAGE`
- `runtime.gpu = true`: passes `--gpus all` to `docker run`
- `runtime.timeout_ms`: agent timeout in ms

## 3. Agents

An agent implements the `Agent` protocol: `prepare()`, `execute()`, `cleanup()`. Which agent to use is controlled by `settings.agent.agent_type`.

| Type | Description |
|---|---|
| `claude_sdk` | calls the Claude API via SDK or delegates to a Python target / CLI |
| `cli` | runs an arbitrary command, passes prompts via stdin and env vars |
| `python` | calls a `module:attribute` Python callable in-process |
| `remote` | HTTP POST to an external agent server |
| `mcp_client` | MCP client (Claude Code, Codex, or custom) |
| `mock` | always returns success; for testing without a real model |

All agents return an `AgentResult` with `model`, `exit_code`, `output`, and `message_count`.

## 4. Workspace lifecycle

```plaintext
AEBENCH_EPHEMERAL_WORKSPACE_ROOT/
└── ae_workspace_<safe_id>_<random>/   # tempdir
    └── workspace/                     # actual workspace, populated from source
        ├── README.md                  # agent reads this
        ├── <source files>
        └── <safe_id>_summary.md       # agent writes its summary here
```

A fresh workspace is created for every run. If `--cleanup-workspace` is passed and the task succeeded, the temp directory gets deleted after the oracle finishes. If the task failed and `AEBENCH_PRESERVE_FAILED_WORKSPACE=true`, the directory is kept for inspection.

### Re-evaluating a completed run

Oracle code can change after an agent run. To preserve the Docker environment for
later evaluation, set this in the case manifest before running the agent:

```toml
[run.runtime]
mode = "docker"
keep_committed_snapshot = true
```

Re-run the current oracle against the recorded workspace and Docker snapshot:

```bash
aebench case oracle <case-id> --run-dir <completed-run-directory>
```

AEBench writes each result under
`<completed-run-directory>/oracle-evaluations/`. It does not replace the original
`case_result.json` or `oracle_result.json`. Each evaluation records the source
revision, workspace path, and runtime snapshot.

The completed run directory must contain `result.jsonl` or `case_result.json`.
Re-evaluation uses the workspace currently present at the recorded path, so
changes made after the run affect the new result. For Docker runs, the saved
image must be available to the current Docker daemon. Remove a saved image when
it is no longer needed:

```bash
docker image rm <saved-image>
```

## 5. Workspace sources

The artifact source is specified in `case.toml` under `[upstream]`. The `sources.py` module sets up the workspace accordingly:

| `source_type` | What it does |
|---|---|
| `git` | clones from `upstream.url` at `upstream.ref`. Uses a local git cache under `~/.cache/aebench/git` |
| `local` | copies from a local path |
| `archive` | downloads or reads a `.tar.gz` / `.zip` and extracts it |
| `overlay` | starts from a base source, then merges a local directory on top |

The `artifact_mode` setting in `aebench.toml` controls wheter the system prefers a vendored local copy (`vendor`), always fetches from upstream (`pointer`), or overlays local changes on the upstream (`hybrid`).

## 6. Prompting

The agent receives two prompts:
- **System prompt**: environment info (local vs Docker), task text, timeout rules, output instructions, and any `prompt_append` additions
- **Initial prompt**: a short imperative telling the agent to start working

The prompt profile (`PromptProfile`) controls which templates get used. `artifact-eval-v1` auto-selects the local or Docker template based on runtime mode.

Rendered prompts are written to `<output_dir>/<safe_id>_prompt.md` so users can inspect what exactly the agent received.

## 7. Output files

Every task run writes these files to its output directory:

- `case_result.json`: full `CaseRunResult` (runtime + oracle results)
- `<safe_id>_report.md`: human-readable summary with status and timings
- `<safe_id>_prompt.md`: the exact prompts sent to the agent
- `<safe_id>.log`: captured stdout/stderr from the agent
- `transcript.jsonl`: full conversation transcript (if the agent supports it)
- `runner.log`: infrastructure-level log messages
- `result.jsonl`: `RunResult` in JSON Lines format
- `oracle_result.json`: full oracle result

For benchmark runs, the output directory additionally contains `benchmark_results.jsonl`, `benchmark_summary.json`, and `benchmark_summary.md`.
