# AE Agent (ArtEval sub-agent)

This agent is the **ae-agent** logic integrated as a sub-agent of the system-intelligence-benchmark ArtEval benchmark. It uses the Claude Agent SDK to run artifact evaluation tasks inside the benchmark container. Code is synced from the standalone [ae-agent](https://github.com/Couen/ae-agent) repo.

## Files

- **install.sh**: Installs `claude-agent-sdk==0.1.24` and configures `~/.claude/settings.json` (48h Bash timeout).
- **runner.sh**: Entry point invoked as `runner.sh <model> <task_or_path>`. Forwards to `runner.py`. Uses `/agent/current_task.txt` when the benchmark passes task via file.
- **runner.py**: Runs the task with Claude Agent SDK; supports rate-limit retry (429), message_formatter; second argument can be task text or path to file.
- **run_eval.py**: Orchestration for one task: `env='local'` runs on host, otherwise runs in Docker (requires swerex/swe-rex).
- **main.py**: CLI entry for batch runs from JSONL; supports both host and Docker per task (see “Run on host (local)” below).
- **utils.py**: `DEFAULT_TIMEOUT_MS`, task/path helpers, Tee, reports, summary (used by runner, main, run_eval).
- **interactive_runner.py**: Interactive multi-turn session inside container (e.g. `docker exec -it <cid> python3 /agent/interactive_runner.py <model>`).
- **__init__.py**: Package marker.

## Usage from the benchmark

From the benchmark root (`benchmarks/arteval_bench/`):

```bash
python src/main.py -i ./data/benchmark/arteval_tasks.jsonl -a ae_agent -m claude-sonnet-4-5-20250929 -o ./outputs/ae_agent_run
```

Or use the helper script from `data/benchmark/`:

```bash
./data/benchmark/run_ae_agent.sh [model_name]
```

The benchmark will:

1. Upload the agent to `/agent` in the container.
2. For ae_agent: upload task to `/agent/current_task.txt`, then run `runner.sh "$model" /agent/current_task.txt` (avoids shell quoting with large tasks).
3. Use long-running and live-log behavior (48h timeout, live log streaming, `_agent_eval` removal before run and re-upload before evaluation, container kept for debugging).
4. Pass through `ANTHROPIC_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_FOUNDRY_BASE_URL`, `CLAUDE_CODE_USE_FOUNDRY` when set.

## Dependencies

- Python 3 with `claude-agent-sdk` (installed by `install.sh`).
- Optional: `message_formatter` for prettier output (if present in the environment).

## Run on host (local)

You can run tasks **on the host machine** (no Docker) from this directory:

1. **Single-task / batch via main.py**  
   Use a JSONL input where each line can set `"env": "local"` or `"run_on_host": true` to run that task on the host. Other lines without that run in Docker (if swerex is available).

   ```bash
   cd benchmarks/arteval_bench/src/agents/ae_agent
   python main.py -i /path/to/tasks.jsonl -a ae_agent -m claude-sonnet-4-5-20250929 -o ./outputs/host_run
   ```

2. **Requirements for host mode**  
   - `ANTHROPIC_API_KEY` or `ANTHROPIC_FOUNDRY_API_KEY` set  
   - Docker installed and running (for prereq check; agent runs on host)  
   - `pip install claude-agent-sdk`

3. **Docker mode from this directory**  
   If JSONL has `"env": "docker"` (or no `run_on_host`), `main.py` will run that task in Docker via `run_eval.py` (requires `swe-rex` / `swerex`).

## Relation to standalone ae-agent repo

The standalone ae-agent repo provides the same host/Docker CLI. This sub-agent includes both the **in-container** runner (used by the benchmark’s `run_eval_in_env.py`) and the **host/local** mode via `main.py` and `run_eval.py`.
