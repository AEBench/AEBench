# Adding an Agent

AEBench supports multiple agent backends. This guide covers all the ways to plug in an agent, from a config-only change to writing a custom agent class.

## 1. How agents get invoked

`TaskRunner` assembles two things and passes them to the agent:

1. **`AgentRequest`** — model name, system prompt, initial prompt, timeout, agent-specific options
2. **`RunSession`** — workspace paths, runtime context (local vs Docker), infrastructure handles

The agent translates these into actual model calls and returns an `AgentResult` with exit code, output text, and message count.

Which agent to use is controlled by `settings.agent.agent_type`, which can be set in config files or environment variables.

## 2. CLI agent (easiest, no code needed)

The `cli` agent runs any external command and feeds it the prompts via environment variables and stdin. Simplest way to connect a new agent if it already has a CLI.

```toml
# aebench.toml or ~/.config/aebench/config.toml
[agent]
agent_type = "cli"

[agent.cli]
argv = ["my-agent", "--some-flag"]
env = {"MY_AGENT_API_KEY" = "..."}
```

Or using environment variables:
```bash
export AEBENCH_AGENT_KIND=cli
export AEBENCH_AGENT_CLI_ARGV='["my-agent", "--some-flag"]'
aebench case run my-case
```

The CLI agent sets `AEBENCH_SYSTEM_PROMPT`, `AEBENCH_INITIAL_PROMPT`, `AEBENCH_WORKSPACE` as environment variables, passes the initial prompt on stdin, and captures stdout+stderr as the agent output.

## 3. Python agent

The `python` agent calls a Python callable in-process. Useful if your agent is a library or you want to avoid subprocess overhead.

```toml
[agent]
agent_type = "python"

[agent.python]
target = "my_agent.runner:run_agent"
```

The target must be importable as `module:attribute`. The callable should accept these kwargs:

```python
def run_agent(*, prompt: str, cwd: Path, env: dict, timeout: float | None) -> str:
    result = my_model_library.complete(user=prompt, cwd=str(cwd), timeout=timeout)
    return result.text
```

Return value can be a `str` (treated as output, exit code 0), a `dict` (validated as AgentResult fields), or an `AgentResult` directly.

## 4. Remote agent

The `remote` agent sends a JSON POST request to an HTTP endpoint and reads back an `AgentResult`.

```toml
[agent]
agent_type = "remote"

[agent.remote]
base_url = "http://my-agent-server:8080/run"
auth = "Bearer my-token"
```

Request body:
```json
{
  "model": "...",
  "system_prompt": "...",
  "initial_prompt": "...",
  "workspace_path": "/path/to/workspace",
  "timeout_ms": 3600000,
  "interactive": false
}
```

Response must be a JSON object matching the `AgentResult` schema (`model`, `exit_code`, `output`, `message_count`).

## 5. MCP client agent

For Claude Code, Codex CLI, and other MCP-compatible tools:

```toml
[agent]
agent_type = "mcp_client"

[agent.mcp]
client = "claude_code"
argv = ["claude", "--dangerously-skip-permissions"]
mcp_mode = "mcp_local"
```

## 6. Writing a custom agent class

If none of the built-in agents fit, users can implement the `Agent` protocol directly:

```python
from runtime.driver import Agent
from models import AgentRequest, AgentResult

class MyAgent:
    name: str = "my_agent"

    def prepare(self, session, listener=None) -> None:
        pass

    def execute(self, request: AgentRequest, session, listener=None) -> AgentResult:
        # session.host_workspace — workspace path on host
        # session.runtime_workspace — workspace path inside the container
        # request.system_prompt, request.initial_prompt — prompts
        # request.timeout_ms — timeout
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

Then register it in `runtime/driver.py`'s `get_agent()` function:

```python
if agent_type == AgentType.MY_AGENT:
    return MyAgent()
```

And add `MY_AGENT = "my_agent"` to the `AgentType` enum in `settings.py`.

> [!NOTE]
> Before writing a custom agent, try the `python` agent with a thin wrapper function. It gets you the same result with less code and no changes to the core.

## 7. RunSession attributes

The `session` argument passed to `prepare()` and `execute()` is a `RunSession` frozen dataclass. Key attributes:

- `host_workspace` — workspace path on the host
- `runtime_workspace` — workspace path inside the container (`"/repo"` for Docker)
- `host_refs` / `runtime_refs` — refs directory paths
- `summary_path` — where the agent should write its summary
- `task_id` — shortcut for `run_spec.id`
- `timeout_ms` — shortcut for `run_spec.runtime.timeout_ms`
- `runtime_backend` — the active runtime backend (Docker or local); use it to run commands inside the container

## 8. Testing

Use the `mock` agent to verify the rest of the pipeline works before connecting a real model:

```bash
export AEBENCH_AGENT_KIND=mock
aebench case run my-case
```

The mock agent always returns exit code 0. This lets you verify workspace setup, oracle logic, and output files without spending API tokens. Once it works, swap in the real agent.

## 9. Summary

| Option | When to use |
|---|---|
| `cli` | agent has a command-line interface; simplest |
| `python` | agent is a Python library; no subprocess overhead |
| `remote` | agent runs on a separate server |
| `mcp_client` | Claude Code, Codex, or other MCP tool |
| `claude_sdk` | direct Claude API access (the default) |
| custom class | you need full control over the execution |
