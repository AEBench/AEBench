# Adding an Agent Harness

AEBench invokes Codex and Claude Code through shell harnesses. Each harness receives two environment variables:

- `PROMPT`: the complete AEBench prompt
- `AGENT_CONFIG`: the selected model name

The CLI runs without interactive input and writes raw JSON output to `runner_output.log`. GNU `timeout` terminates it when the `run.runtime.timeout_ms` limit expires.

## Built-in harnesses

| `--agent` | Authentication |
| --- | --- |
| `codex` | `CODEX_API_KEY` or `OPENAI_API_KEY` |
| `claude` | `ANTHROPIC_API_KEY` |
| `codex_non_api` | ChatGPT subscription from `~/.codex/auth.json` |
| `claude_non_api` | Claude subscription OAuth token |

Always pass the model with `--model`. API and subscription modes use separate agent names. This prevents a run from selecting another credential type without an explicit CLI option.

## Codex subscription

Authenticate the host CLI first:

```bash
codex login
codex login status
```

`codex_non_api` copies `~/.codex/auth.json` into a private per-run home. Set `AEBENCH_CODEX_AUTH_FILE` to use another auth file. Codex can update the copy when it refreshes a token. AEBench deletes the copy after the agent exits.

## Claude subscription

Generate a long-lived token using Claude Code:

```bash
claude setup-token
```

Supply it directly or through a file:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...
# or
export AEBENCH_CLAUDE_OAUTH_TOKEN_FILE=~/.config/aebench/claude_oauth_token
```

AEBench copies the token into the private per-run home and deletes it after the agent exits.

The CLI can read subscription credentials while it runs without command approval. Use dedicated, revocable credentials. Run trusted case definitions on a disposable worker. With `--allow-host-docker`, a case can control the host through the Docker daemon.

## Adding another CLI

1. Add `src/runtime/agent_scripts/<name>/solve.sh`.
2. Add the name to `AgentName` and `AGENT_NAMES` in `models.py`.
3. Pass only the credentials required by that harness in `_agent_env()`.
4. Add unit tests for the command, environment, time limit, and raw output.

Keep source preparation and scoring outside the solve script. The harness runs the agent. AEBench prepares the source, manages the runtime, runs the oracle, and stores the result.
