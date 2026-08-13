# Agent Harnesses

AEBench invokes Codex and Claude Code through shell scripts. Each script
receives two environment variables:

- `PROMPT`: the complete AEBench prompt
- `AGENT_CONFIG`: the selected model name

AEBench starts each agent CLI without prompts for user input and writes its JSON
output to `runner_output.log`. GNU `timeout` terminates the CLI when the
`run.runtime.timeout_ms` limit expires.

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

For each case run, AEBench creates a temporary `HOME` directory and copies the
selected auth file into it. Codex may update this copy when it refreshes a
token. AEBench deletes the directory after the agent exits. The original auth
file remains unchanged.

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

For each case run, AEBench creates a temporary `HOME` directory and copies the
token into it. AEBench deletes the directory after the agent exits. The
original token file remains unchanged.

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

Replace the example case ID with any case listed by `uv run aebench case list`.

The agent CLI can read its credential and can run commands without approval.
Use a credential that you can revoke and a disposable Chameleon instance.

Some case manifests set `[run.artifact_requirements] docker = true` because the
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

## Adding another CLI

1. Add `src/runtime/agent_scripts/<name>/solve.sh`.
2. Add the name to `AgentName` and `AGENT_NAMES` in `models.py`.
3. Pass only the credentials required by that harness in `_agent_env()`.
4. Add unit tests for the command, environment, time limit, and CLI output.

The solve script starts the agent. AEBench prepares the source, manages the
runtime, runs the oracle, and stores the result.
