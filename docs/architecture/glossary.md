# Glossary

Quick reference for the main types and functions. Not everything is listed here: just the ones you are most likely to encounter when working with AEBench or writing oracle code.

## Models (in `models.py`)

- **CaseConfig**: the full case definition loaded from `case.toml`. Contains the case ID, case brief, run config, oracle config, and upstream source info.
- **TaskConfig**: a single task's run parameters: source, instructions path, runtime settings, prompt profile. Nested inside `CaseConfig` as `case.run`.
- **OracleConfig**: oracle settings: expected score, phase list, failure mode, score mode.
- **RuntimeConfig**: runtime mode (docker/local), image, timeout, GPU flag.
- **OracleInput**: context passed to oracle phase classes: case_dir, artifact_dir, workspace_dir, output_dir, runtime_result.
- **PromptArgs**: inputs for the Jinja2 prompt renderer: task text, workspace path, runtime mode, timeout, etc.
- **RunResult**: outcome of one agent task run: status, timings, workspace path, agent output.
- **OracleResult**: outcome of oracle evaluation: status, score, per-phase results.
- **CaseRunResult**: combined runtime + oracle result for one case.
- **AgentRequest**: what gets sent to an agent: model, prompts, timeout, agent type, options.
- **AgentResult**: what comes back from an agent: model, exit_code, output, message_count.

## Config loading (in `project_config.py` and `config.py`)

- **ProjectConfig**: top-level project config loaded from `aebench.toml`.
- **AgentSettings**: agent config block from TOML: agent_type, model, driver-specific sub-configs.
- **Config**: the fully resolved runtime config (merges project config + user config + env vars).
- **AgentConfig**: the fully resolved agent config inside `Config`.
- **AppState**: holds `ProjectState` + `Config`. Created once per CLI command.

## Runtime (in `runtime/`)

- **CaseRunner**: runs one case: task execution + oracle evaluation.
- **TaskRunner**: runs one agent task: workspace setup prompt build agent launch result.
- **BenchmarkRunner**: runs multiple cases and writes a summary.
- **Agent** (Protocol): interface for agent implementations. Has `prepare()`, `execute()`, `cleanup()`.
- **MockAgent**, **CliAgent**, **PythonAgent**, **RemoteAgent**: built-in agent implementations.
- **RunSession**: frozen dataclass with per-task state shared between runtime backend and agent.
- **BenchRuntime** (Protocol): interface for Docker/local runtime backends.
- **get_runtime()**: factory that returns the right backend for a given runtime mode.
- **get_agent()**: factory that returns the right agent for the current settings.

## Oracle (in `evaluator/oracles/`)

- **BaseCheck**: abstract base for all oracle checks. Subclass this and implement `check()`.
- **CheckResult**: pass/fail outcome of one check, with message, stdout/stderr, exit code.
- **CheckEntry**: one row in an oracle report: name, outcome (passed/failed/warning), message.
- **CheckOutcome**: enum: PASSED, FAILED, WARNING.
- **OracleReport**: collects CheckEntry objects from a phase and exposes pass/fail counts.
- **CaseOracleEnvSetupBase**, **CaseOracleArtifactBuildBase**, **CaseOracleBenchmarkPrepBase**, **CaseOracleExperimentRunsBase**: base classes for case oracle phases. Inherit from these.
- **run_phases()**: runs discovered oracle phases and returns an `OracleResult`.
- **discover_oracle_phases()**: finds oracle classes in a case's `oracles/` directory.

## Enums (in `settings.py`)

- **AgentType**: `claude_sdk`, `mock`, `python`, `cli`, `remote`, `mcp_client`
- **McpMode**: `mcp_local`, `mcp_host_bridge`
- **McpClientKind**: `claude_code`, `codex`, `custom`
- **LogLevel**: `debug`, `info`, `warning`, `error`
