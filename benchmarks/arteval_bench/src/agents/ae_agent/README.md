# AE Agent (ArtEval sub-agent)

This agent is the **ae_agent** for the system-intelligence-benchmark ArtEval benchmark, with the same logic as the standalone [ae-agent](https://github.com/Couen/ae-agent) repo. It runs inside the benchmark container using the Claude Agent SDK to execute artifact evaluation tasks.

## Files

- **install.sh**: Installs `claude-agent-sdk` inside the container for use by runner.py.
- **runner.sh**: Entry script; invoked as `runner.sh <model> <task_or_path>`. Uses `/agent/current_task.txt` when the benchmark passes the task via file.
- **runner.py**: Runs the task with Claude Agent SDK; supports 429 rate-limit retry; second argument can be task text or path to a task file. Artifact path in container is `/repo`.
- **run_eval.py**: Single-task orchestration: `env='local'` runs on host, otherwise runs in Docker (requires swerex/swe-rex).
- **main.py**: CLI entry for batch runs from JSONL; supports host or Docker per task.
- **utils.py**: Timeout, task/path helpers, Tee, reports, summary (used by runner, main, run_eval).
- **__init__.py**: Package marker.

## Usage from the benchmark

From the benchmark root (`benchmarks/arteval_bench/`):

```bash
python src/main.py -i ./data/benchmark/arteval_tasks.jsonl -a ae_agent -m claude-sonnet-4-5-20250929 -o ./outputs/ae_agent_run
```

You can also use `-a ae-agent`; it is equivalent to `ae_agent`.

The benchmark will:

1. Upload this agent to `/agent` in the container.
2. For ae_agent: write the task to `/agent/current_task.txt`, then run `runner.sh "$model" /agent/current_task.txt` (avoids shell quoting issues with large tasks).
3. Use long-running and live-log behavior (48h timeout, streamed logs, remove `_agent_eval` before run and re-upload before evaluation, container kept for debugging).
4. **Evaluation script flow** (same as claude_sdk): after the agent finishes, run the JSONL `evaluator` (test_method), e.g. `cd /repo && python _agent_eval/main.py`, parse output for `score` and write to result.
5. If set, pass through `ANTHROPIC_API_KEY`, `ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_FOUNDRY_BASE_URL`, `CLAUDE_CODE_USE_FOUNDRY`.

**Evaluation flow on host**: When `run_on_host=True` and the agent is ae_agent, `run_eval_in_env.run_eval_on_host` calls this package’s `run_agent_then_eval()`: run the agent first, then run `test_method` on the host (e.g. `cd project_path && python _agent_eval/main.py`), parse score with `utils.parse_eval_score()`, and return a result with the same shape as the Docker path (`score`, `test_method`, `status`).

## Dependencies

- Python 3; `claude-agent-sdk` is installed in the container via `install.sh`.
- When running in Docker via the benchmark’s `run_eval_in_env.py`, install `swerex` on the host (the benchmark includes it). When using this directory’s `main.py` for Docker mode standalone, you also need `swe-rex`.

## Running on host (local)

You can run tasks on the **host** from this directory (without the benchmark’s Docker flow):

1. **Single or batch via main.py**  
   Use a JSONL where each line can set `"env": "local"` or `"run_on_host": true` to run that task on the host; others run in Docker (requires swerex).

   ```bash
   cd benchmarks/arteval_bench/src/agents/ae_agent
   python -m ae_agent.main -i /path/to/tasks.jsonl -a ae_agent -m claude-sonnet-4-5-20250929 -o ./outputs/host_run
   ```

2. **Host mode requirements**  
   - Set `ANTHROPIC_API_KEY` or `ANTHROPIC_FOUNDRY_API_KEY`  
   - Docker installed and running (for prereq check; agent runs on host)  
   - `pip install claude-agent-sdk`

3. **Docker mode from this directory**  
   If the JSONL has `"env": "docker"` (or `run_on_host` is not set), `main.py` runs that task in Docker via `run_eval.py` (requires `swe-rex`/`swerex`).

## Relation to the standalone ae-agent repo

The standalone ae-agent repo provides the same host/Docker CLI. This sub-agent includes both the **in-container** runner (used by the benchmark’s `run_eval_in_env.py`) and **host/local** mode via `main.py` and `run_eval.py`.
