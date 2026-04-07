# AEBench Overview

AEBench evaluates AI agents on *artifact evaluation* (AE) tasks: given the source code and instructions from a peer-reviewed paper, can an agent reproduce the paper's key results?

## 1. Cases

The unit of work in AEBench is called a **case**. Each case is a directory (sometimes called a bundle) that packages everything needed to evaluate one research artifact:

```plaintext
cases/eurosys25_egwalker/
├── case.toml          # manifest: identity, source, runtime, oracle config
├── refs/              # reference data (checksums, expected results, etc.)
│   ├── datasets.ref.json
│   └── timings.ref.json
├── artifact/          # optional local copy of the artifact
│   └── README.md
└── oracles/           # evaluation logic, one file per phase
    ├── common.py      # shared helpers for this case
    ├── env_setup.py
    ├── artifact_build.py
    ├── benchmark_prep.py
    └── experiment_runs.py
```

`case.toml` is the central configuration file. It declares the case ID, where to fetch the artifact from (git URL, local path, or archive), runtime parameters (Docker image, timeout), and oracle settings.

## 2. The four evaluation phases

Every case uses the same four-phase structure, always in this order:

1. **env_setup** — required tools and their versions, environment variables, directory structure
2. **artifact_build** — build commands succeed, expected binaries or modules are present
3. **benchmark_prep** — datasets downloaded, checksums match, instrumentation hooks work
4. **experiment_runs** — experiments ran and produce results within tolerance of the reference

Each phase that passes scores one point. Most cases have all four phases, so the expected score is 4. The default `failure_mode` is `fail_fast`, which means if a phase fails the remaining ones are skipped (marked `PENDING`). This makes sense because you cant build without the environment, cant run experiments without the build, and so on.

## 3. How a case run works

When you run a case, this is roughly what happens:

```
CLI
  │
  ▼
CaseRunner.run()
  ├── load case.toml → CaseConfig
  ├── prepare workspace (clone repo / copy files / extract archive)
  ├── build prompt (system prompt + task instructions)
  ├── TaskRunner.run() → launches agent
  │     └── Agent.execute() → agent does the work → RunResult
  └── OracleRunner.execute() → runs four oracle phases → OracleResult
        └── writes case_result.json
```

The agent reads the artifact's README (or whatever `instructions_path` points to), performs the actual work inside the workspace — installing dependencies, building, downloading data, running experiments — and writes a brief summary file. After the agent exits, the oracle inspects the workspace and scores what was accomplished.

## 4. Source layout

```plaintext
src/
├── cli.py                  # CLI entry point (the aebench command)
├── models.py               # all shared Pydantic models
├── config.py               # resolved runtime settings
├── project_config.py       # project/workspace/user config loading from TOML
├── constants.py            # file-name templates, defaults
├── settings.py             # enum definitions (AgentType, McpMode, etc.)
├── prompting.py            # Jinja2 prompt template rendering
├── sources.py              # workspace setup (git clone, copy, archive extract)
├── task_loader.py          # instruction text loading + case brief injection
├── utils.py                # safe_name, Tee, send_event
├── run_control.py          # interrupt / stop flag
├── log.py                  # logging config
├── git.py                  # git bundle / checkout / cache helpers
│
├── runtime/                # task execution layer
│   ├── case_runner.py      # runs one case: task + oracle
│   ├── task_runner.py      # runs agent (workspace → prompt → agent → result)
│   ├── benchmark_runner.py # runs multiple cases, writes summary
│   ├── oracle_runner.py    # in-process and subprocess oracle runners
│   ├── backend.py          # Docker and local runtime backends
│   ├── driver.py           # agent implementations + factory
│   ├── session.py          # RunSession: shared state for one task run
│   ├── workspace.py        # temp workspace creation and cleanup
│   ├── cases.py            # case resolution, spec loading, task creation
│   └── reporting.py        # output paths, report writing
│
├── evaluator/              # oracle layer
│   ├── loader.py           # load and validate case.toml → CaseConfig
│   └── oracles/
│       ├── discovery.py    # discovers concrete oracle classes in a case
│       ├── execution.py    # instantiates + runs phases, produces OracleResult
│       ├── case_base.py    # CaseOracleXxxBase classes with path helpers
│       ├── env_setup_checks.py        # check classes for env setup
│       ├── artifact_build_checks.py   # check classes for build
│       ├── benchmark_prep_checks.py   # check classes for bench prep
│       ├── experiment_runs_checks.py  # check classes for experiment runs
│       ├── requirements_common.py     # shared check types
│       └── utils.py                   # BaseCheck, CheckResult, OracleReport
│
└── console/
    └── dashboard.py        # Rich live display for benchmark runs
```

## 5. Configuration

Settings are resolved from several layers. Later layers take priority over earlier ones:

1. **`cases.json`** — case registry (which cases exist and where they live)
2. **`aebench.toml`** — workspace-level config (checked into the repo)
3. **`~/.config/aebench/config.toml`** — user config (per developer machine)
4. **Environment variables** — override anything at runtime

The workspace is discovered by walking up from the current working directory until `aebench.toml` or `cases.json` is found.

Some commonly used environment variables:

| Variable | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | API key for Claude SDK agent |
| `AEBENCH_DEFAULT_MODEL` | default model name |
| `AEBENCH_AGENT_KIND` | agent type (`claude_sdk`, `cli`, `python`, `remote`, `mcp_client`) |
| `AEBENCH_DEFAULT_TIMEOUT_MS` | per-task timeout in milliseconds |
| `AEBENCH_EPHEMERAL_WORKSPACE_ROOT` | where to create temp workspaces |
| `AEBENCH_PRESERVE_FAILED_WORKSPACE` | keep workspace on failure (`true`/`false`) |
| `AEBENCH_DEFAULT_DOCKER_IMAGE` | default Docker image for Docker-mode tasks |
