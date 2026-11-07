# Agent Harnesses

An agent harness is a directory under `agents/`. AEBench finds every directory
there and runs the selected one with `--agent <name>`; nothing in AEBench's
code names a specific agent. `aebench agent list` shows the agents found and
reports any problem with their files.

AEBench starts the agent without prompts for user input and writes its
timestamped output to `runner_output.log`. GNU `timeout` terminates the agent
when the `run.runtime.timeout_ms` limit expires.

The AEBench runtime image includes the Codex and Claude Code CLIs. Install
Docker on the Chameleon instance. No agent CLI installation is required there.

## Available harnesses

| `--agent` | Authentication |
| --- | --- |
| `codex` | `CODEX_API_KEY` or `OPENAI_API_KEY` |
| `claude` | `ANTHROPIC_API_KEY` |
| `codex_non_api` | ChatGPT subscription from `~/.codex/auth.json` |
| `claude_non_api` | Claude subscription OAuth token |

Pass the model with `--model`. AEBench passes this value to the selected
agent CLI. This implementation was tested with `gpt-5.5` and
`claude-opus-4-8`. Use a model available to the selected account.

API and subscription modes use separate agent names. Selecting `codex` or
`claude` requires an API key. Selecting `codex_non_api` or `claude_non_api`
requires a subscription credential.

## Codex subscription

On your local computer, authenticate with Codex:

```bash
codex login
codex login status
```

Copy the generated auth file to the Chameleon instance:

```bash
scp ~/.codex/auth.json cc@<floating-ip>:~/codex_auth.json
```

On the Chameleon instance, restrict access to the file and select it for the
run:

```bash
mkdir -p ~/.config/aebench
chmod 700 ~/.config/aebench
install -m 600 ~/codex_auth.json ~/.config/aebench/codex_auth.json
rm ~/codex_auth.json
export AEBENCH_CODEX_AUTH_FILE=~/.config/aebench/codex_auth.json

uv run aebench case run asplos24_gaia \
  --agent codex_non_api \
  --model gpt-5.5
```

If Codex is installed on the Chameleon instance, you can run `codex login`
there. AEBench reads `~/.codex/auth.json` by default.

For each case run, AEBench copies the selected auth file into a temporary
support directory outside the artifact workspace. Codex may update this copy
when it refreshes a token. AEBench deletes the directory after the agent exits.
The original auth file remains unchanged.

## Claude subscription

On your local computer, generate a long-lived token:

```bash
claude setup-token
```

Copy the token printed by this command into
`~/.config/aebench/claude_oauth_token`. The file must contain only the token.
Restrict access to the file, then copy it to the Chameleon instance:

```bash
chmod 600 ~/.config/aebench/claude_oauth_token
scp ~/.config/aebench/claude_oauth_token cc@<floating-ip>:~/claude_oauth_token
```

On the Chameleon instance, restrict access to the token and select it for the
run:

```bash
mkdir -p ~/.config/aebench
chmod 700 ~/.config/aebench
install -m 600 ~/claude_oauth_token ~/.config/aebench/claude_oauth_token
rm ~/claude_oauth_token
export AEBENCH_CLAUDE_OAUTH_TOKEN_FILE=~/.config/aebench/claude_oauth_token

uv run aebench case run osdi24_kondo \
  --agent claude_non_api \
  --model claude-opus-4-8
```

You can also set `CLAUDE_CODE_OAUTH_TOKEN` directly on the Chameleon instance.

For each case run, AEBench copies the token into a temporary support directory
outside the artifact workspace. AEBench deletes the directory after the agent
exits. The original token file remains unchanged.

## API authentication

Set the provider key on the Chameleon instance and select the matching API
harness:

```bash
export OPENAI_API_KEY=...
uv run aebench case run asplos24_gaia \
  --agent codex \
  --model gpt-5.5

export ANTHROPIC_API_KEY=...
uv run aebench case run osdi24_kondo \
  --agent claude \
  --model claude-opus-4-8
```

Replace the example case ID with a case ID from `cases.json`.

The agent CLI can read its credential and can run commands without approval.
Use a credential that you can revoke and a disposable Chameleon instance.

Some `case.toml` files set `[run.artifact_requirements] docker = true` because the
artifact must build or run its own Docker image. Add `--allow-host-docker` only
for those cases:

```bash
uv run aebench case run asplos24_crocus \
  --agent codex_non_api \
  --model gpt-5.5 \
  --allow-host-docker
```

This option mounts the host Docker socket and gives the agent control of the
Chameleon instance's Docker daemon.

## Adding an agent

Adding an agent needs no change to AEBench: create a directory, rebuild the
runtime image, and run it.

```
agents/<name>/
  agent.sh      required: starts the agent CLI
  agent.toml    what AEBench must provide: credentials, required binaries, ...
  install.sh    installs the CLI into the runtime image
  ...           any other files agent.sh needs (config templates, helpers)
```

The directory name is the `--agent` value (lowercase letters, digits, `_`, `-`).
For the smallest working example, see `agents/claude/`.

### agent.sh

AEBench copies the agent directory into the run and runs `agent.sh` with bash,
as the unprivileged `agent` user inside the workspace, with stdin from
`/dev/null`. Whatever it prints becomes `runner_output.log`; its exit code is
the agent's exit code (0 means success).

| Variable | Value |
| --- | --- |
| `PROMPT` | the complete AEBench prompt (also in the file `$AEBENCH_PROMPT_FILE`) |
| `AEBENCH_MODEL` | the `--model` value |
| `AEBENCH_REASONING_EFFORT` | `reasoning_effort` from `agent.toml`, if set |
| `AEBENCH_AGENT_DIR` | the run's copy of the agent directory, for templates and helpers |
| `AEBENCH_STATE_DIR` | where the CLI keeps its config, sessions and caches; deleted after the run |
| `HOME` | `/home/agent` in Docker; your own home directory for `mode = "local"` cases |
| declared secrets | each `[[secrets]]` entry, as an ordinary environment variable |

Rules for the script:

- Start the CLI in its non-interactive mode with every permission prompt
  disabled; the container is the sandbox. Turn off the CLI's own sandbox if it
  has one.
- Point the CLI's config directory at `$AEBENCH_STATE_DIR` (for example
  `CLAUDE_CONFIG_DIR`, `CODEX_HOME`). For `mode = "local"` cases, `HOME` is your
  real home directory, so this keeps the run from reading or changing your own
  CLI configuration.
- Print the CLI's machine-readable output (JSON lines) on stdout. That output is
  the only record AEBench keeps of what the agent did.
- Turn off telemetry and uploads the CLI does by default.
- Let long commands finish: raise the CLI's per-command timeout. The prompt tells
  agents not to set their own timeouts.
- Command monitoring (`aebench case monitor`, `--trajectory-audit`) records
  each shell the CLI starts through `/bin/bash`. A CLI that runs one long-lived
  shell and writes commands to it shows up as a single command.

### agent.toml

Every field is optional; an agent without `agent.toml` gets no credentials.

```toml
description = "My CLI with an API key"   # shown by `aebench agent list`
requires = ["mycli"]                     # checked before the agent starts
reasoning_effort = "high"                # exported and recorded with the result
prompt_append = "Extra instructions for this agent only."
trace_format = "claude_code"             # claude_code | codex | raw (default)

# A credential read from the host and exported to agent.sh.
[[secrets]]
name = "MYCLI_API_KEY"                   # the variable agent.sh sees
from_env = ["MYCLI_API_KEY", "OTHER_NAME"]  # host variables, first non-empty wins (default: [name])
from_file_env = "MYCLI_KEY_FILE"         # or a host variable naming a file holding it
hint = "set MYCLI_API_KEY"               # shown when it is missing

# A host file copied into $AEBENCH_STATE_DIR before the run (mode 600).
[[files]]
target = ".mycli/auth.json"
sources = ["${MYCLI_AUTH_FILE}", "~/.mycli/auth.json"]  # first one whose variables are set
hint = "run `mycli login`"
```

A missing secret or file stops the run before anything starts.
Secrets reach the agent through a private file in the support directory, never
on the `docker exec` command line.

`agent.toml` cannot change the container: privileges, mounts, network access
and the shell are the same for every agent, and the case decides whether the
host Docker socket or GPUs are available. Copy files with `[[files]]` instead of
mounting them.

### install.sh

The `Dockerfile` runs every `agents/*/install.sh` as root while it builds the
runtime image, with `AEBENCH_AGENT_PREFIX=/opt/aebench/agents/<name>`. Pin the
CLI's version in the script. Install into the prefix and link the binary into
`/usr/local/bin` when the CLI brings its own runtime, so the tools artifacts
see (Node.js 22, Python) stay unchanged. Several agents can share one CLI; only
one of them needs the install script (`claude_non_api` uses `claude`'s).

Rebuild the image after adding or changing an `install.sh`:

```bash
docker build -t aebench-agent:latest .
```

Changes to `agent.sh` or `agent.toml` do not need a rebuild.

### Trace viewer

`aebench trace export` reads `runner_output.log` according to `trace_format`.
Use `claude_code` or `codex` when the CLI prints the same JSON events as Claude
Code (`--output-format stream-json`) or Codex (`exec --json`). Otherwise the
viewer shows the plain log.

### Checking an agent

```bash
uv run aebench agent list                 # finds the agent and validates agent.toml
uv run pytest tests/unit/test_agents.py   # runs the checks every agent gets
```

Then run a short case with `--agent <name>` and look at `runner_output.log`
and `aebench trace export`.

### Agents outside this repository

Put them in an `agents/` directory next to your project's `aebench.toml`. An
agent name may appear only once across that directory and this repository's
`agents/`. Their
`install.sh` is not run by this repository's `Dockerfile`; build an image from
`aebench-agent:latest` that runs it, and tag that image `aebench-agent:latest`.
