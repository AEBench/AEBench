# AE Agent smoke test

## Purpose

- Test the agent under `src/agents/ae_agent`: **host** and **docker** modes, and the **evaluation script** flow (evaluator runs after the agent and parses score).
- Task is minimal (create `success.txt` with content `1` in the artifact root); finishes in a few minutes and avoids long runs with full arteval_tasks.

## Files

- **ae_agent_smoke/**: Minimal artifact
  - `README.md`: Task description (create success.txt with content 1)
  - `_agent_eval/check.py`: Evaluator; outputs `1` if success.txt exists and contains `1`, else `0`
- **ae_agent_smoke_test.jsonl**: Two lines
  - First line: `run_on_host: true`, run ae_agent + evaluator on host
  - Second line: `run_on_host: false`, run ae_agent + evaluator in Docker

## How to run

From the **benchmarks/arteval_bench** directory:

```bash
# Set ANTHROPIC_API_KEY or ANTHROPIC_FOUNDRY_API_KEY first
python src/main.py \
  -i ./data/benchmark/ae_agent_smoke_test.jsonl \
  -a ae_agent \
  -m claude-sonnet-4-5-20250929 \
  -o ./outputs/ae_agent_smoke_$(date +%Y%m%d_%H%M%S)
```

- **Host task**: Runs the agent on the host, then runs `python3 _agent_eval/check.py` on the host to get the score.
- **Docker task**: Runs the agent in the container, then runs the evaluator in the container to get the score; the container is kept running by default for debugging.

Results are under the `-o` directory: `result.jsonl` (one JSON object per line with `score`, `status`, `test_method`, etc.) and `avg_score.json`.

## Interactive mode

The benchmark’s `src/main.py` does not read an `interactive` field from the JSONL, so the command above only covers **non-interactive** runs. To test interactive mode:

- Use ae_agent’s main entry with `--interactive`, and set `"env": "local"` or `"run_on_host": true` / `"env": "docker"` in the JSONL for the task, for example:
  ```bash
  cd src/agents/ae_agent
  python -m ae_agent.main --interactive -i ../../../data/benchmark/ae_agent_smoke_test.jsonl -o ../../../outputs/ae_agent_smoke_int
  ```
- In interactive mode, after the first task completes you can keep typing instructions; type `quit` or `exit` to end.
