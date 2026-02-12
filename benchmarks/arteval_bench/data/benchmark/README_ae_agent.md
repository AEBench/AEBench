# Run ArtEval Benchmark with AE Agent

This directory contains `arteval_tasks.jsonl` and other benchmark task definitions. To run the benchmark with **ae_agent**, start from the **benchmark root** (`benchmarks/arteval_bench/`).

## Run from benchmark root

```bash
cd benchmarks/arteval_bench

# Use ae_agent with data/benchmark/arteval_tasks.jsonl as input
python src/main.py \
  -i ./data/benchmark/arteval_tasks.jsonl \
  -a ae_agent \
  -m claude-sonnet-4-5-20250929 \
  -o ./outputs/ae_agent_$(date +%Y-%m-%d_%H-%M-%S)
```

Or, if `run.sh` supports passing an agent argument:

```bash
cd benchmarks/arteval_bench
./run.sh claude-sonnet-4-5-20250929 ae_agent
```

## Environment

- Set `ANTHROPIC_API_KEY` or `ANTHROPIC_FOUNDRY_API_KEY`.
- Optional: `ANTHROPIC_FOUNDRY_BASE_URL`, `CLAUDE_CODE_USE_FOUNDRY=1`.
- The ae_agent implementation lives under `src/agents/ae_agent/`, synced with the standalone ae-agent repo (runner, install, utils, interactive_runner).

## Task format

Each line of `arteval_tasks.jsonl` is one JSON object, including at least:

- `artifact_id`, `artifact_dir`, `artifact_readme`, `artifact_url`
- `evaluator`: evaluation command (e.g. `cd /repo && python3 _agent_eval/main.py`)
- `docker_env`: Docker image
- `run_on_host`: when `true`, run on the host instead of Docker
